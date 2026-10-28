#!/usr/bin/env python3
"""Bounded, read-only Docker ownership inventory after failed A171 Deploy V2.

This is evidence for a separate exact cleanup review. It never estimates
reclaimable bytes from image Size or a count of layer diffIDs.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import urllib.request


EXPECTED_DROPLET = ('594077619', 'nyc1')
A171_CANDIDATE = '4ce63a68c668cadd2a0aa7e8aded44ae4a3c46a6'
A171_IMAGE = 'sha256:e61052af13c01d9ce76e92f38b50c794dac8a146427e132d43a8a80554a879aa'
A171_CONTAINER_IDS = {
    '/porota_production_observer': '9b7d41f04b2e7847318c6f8ded0440b9d6a030a5bbb99c3ac5f31c2815beaa95',
    '/porota_production_dashboard': '3d51f57985d8375f6c06c9a57d8a7f6c6dd4b99ee03bdeb0efac440f92e43aa3',
}
STABLE = 'porota-trading-bot:17.0.0-rc6'
OWNED_TAGS = {
    'porota-predeploy-v2:' + A171_CANDIDATE,
    'porota-trading-bot:17.0.0-rc6-candidate-' + A171_CANDIDATE,
}
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
        if (got != iid or type(size) is not int or size < 0 or size > 100 * 1024**3
                or not all(type(v) is list and len(v) <= 256 for v in (tags, digests, layers))
                or any(type(v) is not str or len(v) > 512 for v in tags + digests)
                or any(type(v) is not str or not IMAGE.fullmatch(v) for v in layers)
                or len(layers) != len(set(layers))):
            raise Unverified('IMAGE_RECORD_INVALID')
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
        exact_a171_alias_count=sum(tag in OWNED_TAGS for tag in tags),
        candidate_sha_tokens=sorted(set(rc6_candidate_shas)),
        other_alias_count=other_aliases,
        rc6_repo_digest_count=rc6_digests,
        other_digest_count=len(digests) - rc6_digests)


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
        layers = set(row['layers'])
        other_layers = set().union(*(set(other['layers']) for oid, other in images.items() if oid != iid))
        other_referenced_layers = set().union(*(
            set(other['layers']) for oid, other in images.items()
            if oid != iid and refs[oid]))
        # This is strictly structural overlap. Docker Size is virtual image
        # size, and diffID counts are not measured reclaimable disk bytes.
        out.append(dict(image_id=iid, virtual_size_bytes=row['size'],
            layer_count=len(layers), layers_shared_with_referenced_count=len(layers & other_referenced_layers),
            unique_diffid_count=len(layers - other_layers),
            all_container_refs=len(refs[iid]),
            owned_container_refs=sum(containers[cid][1] in OWNED_CONTAINERS for cid in refs[iid]),
            aliases=aliases,
            cleanup_class=('PROTECTED_REFERENCED' if refs[iid] else
                'PROTECTED_STABLE' if aliases['stable_alias'] else
                'PROTECTED_OTHER_ALIAS' if aliases['other_alias_count'] or aliases['other_digest_count'] else
                'REQUIRES_EXACT_PROVENANCE_REVIEW')))
    owned = {name: next(({'id': cid, 'image': value[0], 'status': value[2],
                         'running': value[3]}
                        for cid, value in containers.items() if value[1] == name), None)
             for name in sorted(OWNED_CONTAINERS)}
    if (any(value is None for value in owned.values()) or
            owned['/porota_production_observer']['image'] != A171_IMAGE or
            owned['/porota_production_dashboard']['image'] != A171_IMAGE or
            any(owned[name]['id'] != expected for name, expected in A171_CONTAINER_IDS.items()) or
            any(value['status'] != 'running' or value['running'] is not True for value in owned.values()) or
            A171_IMAGE not in images or not OWNED_TAGS.issubset(set(images[A171_IMAGE]['tags'])) or
            STABLE not in images[A171_IMAGE]['tags']):
        raise Unverified('A171_RUNTIME_IDENTITY_DRIFT')
    return dict(owned_containers=owned, other_container_count=len(containers)-len(owned),
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
        barrier = barrier_status()
        if barrier != 'ABSENT':
            raise Unverified('DEPLOY_BARRIER_NOT_ABSENT')
        first = snapshot()
        second = snapshot()
        after = shutil.disk_usage('/').free
        if first['raw_ids'] != second['raw_ids'] or barrier != barrier_status():
            raise Unverified('DOCKER_OR_BARRIER_CHANGED_DURING_AUDIT')
        first.pop('raw_ids')
        return dict(schema='RC6_A172_DOCKER_OWNERSHIP_RO_V1', status='GREEN_INVENTORY',
                    read_only=True, droplet=metadata_result, barrier_status=barrier,
                    free_bytes_before=before, free_bytes_after=after,
                    pretransfer_required_a171_bytes=4057135178,
                    current_margin_vs_a171_formula_bytes=after-4057135178,
                    reclaimable_bytes_verified=None, **first)
    finally:
        os.close(fd)


def main():
    try:
        report = audit()
    except (Unverified, OSError, ValueError, TypeError, KeyError, UnicodeError,
            subprocess.TimeoutExpired) as exc:
        reason = str(exc) if isinstance(exc, Unverified) else type(exc).__name__
        report = {'schema': 'RC6_A172_DOCKER_OWNERSHIP_RO_V1',
                  'status': 'UNVERIFIED', 'read_only': True, 'reason': reason}
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'GREEN_INVENTORY' else 2


if __name__ == '__main__':
    raise SystemExit(main())
