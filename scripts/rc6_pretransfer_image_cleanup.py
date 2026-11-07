#!/usr/bin/env python3
"""Canonical pretransfer cleanup: only exclusively tagged, unused RC6 candidates."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import time

IMAGE = re.compile(r'sha256:[0-9a-f]{64}')
CONTAINER = re.compile(r'[0-9a-f]{12,64}')
CANDIDATE = re.compile(r'(?:porota-predeploy-v2:|porota-trading-bot:17\.0\.0-rc6-candidate-)[0-9a-f]{40}')
DB = Path('/opt/porota-trading/data/paper_v17/observer_v17.db')
# The *only* Deploy V2 lock is owned by rc6_deploy_host_mutex.py.  The old
# /tmp spelling was a different inode: an absent /tmp file previously let an
# ungoverned helper proceed while the real promoter owned /run/lock.
LOCK = Path('/run/lock/porota-rc6-deploy-v2.lock')
BARRIER = Path('/run/lock/porota-rc6-deploy-v2-attempt.json')
OWNER_PROOF = 'RC6_DEPLOY_MUTEX_PROOF'
OWNER_FD = 'RC6_DEPLOY_MUTEX_FD'
ROOT_UID = 0  # overridden only by unprivileged OFFHOST tests
RUNTIME = ('porota_production_observer', 'porota_production_dashboard', 'porota_critical_approval_rc6')


class Rejected(RuntimeError):
    pass


def docker(*args):
    result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise Rejected('DOCKER_' + args[0].upper() + '_FAILED')
    if len(result.stdout) > 1024 * 1024:
        raise Rejected('INVENTORY_TOO_LARGE')
    return result.stdout.strip()


@contextmanager
def promotion_lock():
    # The true root-owned mutex is already held by the canonical wrapper and
    # passed to its child as the SAME open file description. Never acquire a
    # second flock: a second descriptor would deadlock against our own parent.
    # Also never mistake a missing mutex/barrier or a /tmp decoy for authority.
    raw_fd = os.environ.get(OWNER_FD, '')
    if not re.fullmatch(r'[3-9][0-9]{0,2}', raw_fd):
        raise Rejected('GOVERNED_MUTEX_FD_MISSING')
    fd = int(raw_fd)
    if fd > 255:
        raise Rejected('GOVERNED_MUTEX_FD_INVALID')
    try:
        attestation = json.loads(os.environ.get(OWNER_PROOF, ''))
    except (ValueError, TypeError):
        raise Rejected('GOVERNED_MUTEX_PROOF_MISSING') from None
    if (not isinstance(attestation, dict) or set(attestation) !=
            {'candidate', 'run', 'attempt', 'phase'} or
            not isinstance(attestation['candidate'], str) or
            re.fullmatch('[0-9a-f]{40}', attestation['candidate']) is None or
            not all(isinstance(attestation[k], str) and
                    re.fullmatch('[1-9][0-9]*', attestation[k]) is not None
                    for k in ('run', 'attempt')) or
            not isinstance(attestation['phase'], str) or
            attestation['phase'] not in {'preclean', 'promotion', 'orphan_gc'}):
        raise Rejected('GOVERNED_MUTEX_PROOF_INVALID')
    parent = os.lstat(LOCK.parent)
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != ROOT_UID or
            (parent.st_mode & stat.S_IWOTH and
             not parent.st_mode & stat.S_ISVTX)):
        raise Rejected('GOVERNED_MUTEX_PARENT_UNSAFE')
    path_stat = os.lstat(LOCK)
    held_stat = os.fstat(fd)
    if (not stat.S_ISREG(path_stat.st_mode) or
            (path_stat.st_dev, path_stat.st_ino) !=
            (held_stat.st_dev, held_stat.st_ino) or
            path_stat.st_uid != ROOT_UID or stat.S_IMODE(path_stat.st_mode) != 0o600 or
            path_stat.st_nlink != 1):
        raise Rejected('GOVERNED_MUTEX_IDENTITY_RED')
    # A fresh independent descriptor MUST observe an already-held flock. Only
    # then reassert LOCK_EX on the inherited descriptor: a competing owner on
    # a different open-file-description would block our inherited descriptor.
    probe = os.open(LOCK, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass  # someone already holds the real lock
        else:
            fcntl.flock(probe, fcntl.LOCK_UN)
            raise Rejected('GOVERNED_MUTEX_NOT_HELD')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Rejected('GOVERNED_MUTEX_OTHER_OWNER') from exc
    finally:
        os.close(probe)
    # The wrapper records the exact attempt/phase before its child and retires
    # it only after the child exits. The sole postfailure orphan-GC exception
    # must have NO attempt barrier and uses the same inherited owner lock.
    try:
        barrier_fd = os.open(BARRIER, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        if attestation['phase'] != 'orphan_gc':
            raise Rejected('GOVERNED_BARRIER_MISSING')
    else:
        try:
            barrier_stat = os.fstat(barrier_fd)
            on_path = os.lstat(BARRIER)
            if (not stat.S_ISREG(barrier_stat.st_mode) or
                    (barrier_stat.st_dev, barrier_stat.st_ino) !=
                    (on_path.st_dev, on_path.st_ino) or
                    barrier_stat.st_uid != ROOT_UID or
                    stat.S_IMODE(barrier_stat.st_mode) != 0o600 or
                    barrier_stat.st_nlink != 1 or barrier_stat.st_size > 1024):
                raise Rejected('GOVERNED_BARRIER_UNSAFE')
            barrier = json.loads(os.read(barrier_fd, 1025))
            if (attestation['phase'] == 'orphan_gc' or
                    not isinstance(barrier, dict) or
                    set(barrier) !=
                        {'candidate', 'run', 'attempt', 'phase', 'status'} or
                    any(barrier.get(key) != attestation[key] for key in attestation) or
                    barrier.get('status') != 'INFLIGHT'):
                raise Rejected('GOVERNED_BARRIER_DRIFT')
        finally:
            os.close(barrier_fd)
    yield


def safety():
    connection = sqlite3.connect(DB.as_uri() + '?mode=ro', uri=True, timeout=1)
    try:
        connection.execute('PRAGMA query_only=ON')
        mode, orders = connection.execute('SELECT mode, real_orders_sent FROM observer_state WHERE id=1').fetchone()
    finally:
        connection.close()
    if mode != 'PRODUCTION_PAPER' or type(orders) is not int or orders != 0:
        raise Rejected('PAPER_SAFETY_UNVERIFIED')
    snapshots = []
    for name in RUNTIME:
        record = list(map(json.loads, docker('inspect', '--format',
            '{{json .Id}}|{{json .Image}}|{{json .State.Running}}|{{json .State.StartedAt}}|{{json .RestartCount}}', name).split('|')))
        if len(record) != 5 or CONTAINER.fullmatch(record[0] or '') is None or IMAGE.fullmatch(record[1] or '') is None or record[2] is not True or not isinstance(record[3], str) or not record[3] or type(record[4]) is not int:
            raise Rejected('RUNTIME_NOT_RUNNING')
        snapshots.append(record)
    return snapshots


def references():
    ids = docker('ps', '-aq', '--no-trunc').splitlines()
    if len(ids) > 128 or any(CONTAINER.fullmatch(cid) is None for cid in ids):
        raise Rejected('CONTAINER_INVENTORY_INVALID')
    images = set()
    for cid in ids:
        value = docker('inspect', '--format', '{{.Image}}', cid)
        if IMAGE.fullmatch(value) is None:
            raise Rejected('CONTAINER_IMAGE_INVALID')
        images.add(value)
    return images


def inspect_image(image):
    values = docker('image', 'inspect', '--format',
                   '{{json .Id}}|{{json .RepoTags}}|{{json .RepoDigests}}|{{json .Config.Labels}}', image).split('|')
    if len(values) != 4:
        raise Rejected('IMAGE_INVENTORY_INVALID')
    identity, tags, digests, labels = map(json.loads, values)
    if identity != image or not isinstance(tags, (list, type(None))) or not isinstance(digests, (list, type(None))):
        raise Rejected('IMAGE_INVENTORY_INVALID')
    if any(not isinstance(tag, str) for tag in (tags or []) + (digests or [])):
        raise Rejected('IMAGE_TAGS_INVALID')
    if labels is not None and (not isinstance(labels, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in labels.items())):
        raise Rejected('IMAGE_LABELS_INVALID')
    return tags or [], digests or [], labels or {}


def eligibility(image):
    tags, digests, labels = inspect_image(image)
    refs = references()  # includes stopped containers, not just running ones
    if image in refs:
        return tags, 'CONTAINER_REFERENCED'
    if not tags or any(CANDIDATE.fullmatch(tag) is None for tag in tags):
        return tags, 'PROTECTED_OR_NONCANDIDATE_ALIAS'
    # Image repo names are not a proof of ownership on a shared Docker host.
    # The immutable Predeploy V2 artifact embeds these two OCI labels, and
    # *every* candidate alias must encode exactly that one commit. Legacy or
    # unlabeled images remain untouched until provenance can be established.
    commits = {tag.rsplit(':', 1)[-1][-40:] for tag in tags}
    if (len(commits) != 1
            or labels.get('porota.predeploy') != 'v2'
            or labels.get('porota.commit') not in commits):
        return tags, 'OWNERSHIP_LABELS_UNVERIFIED'
    # Docker load on the target engine retains a canonical RepoDigest alongside
    # its candidate tag. That immutable spelling is the same repository owner,
    # not an external alias. Unknown or unpaired repositories remain protected.
    repositories = {tag.split(':', 1)[0] for tag in tags}
    for digest in digests:
        match = re.fullmatch(r'(porota-predeploy-v2|porota-trading-bot)@sha256:[0-9a-f]{64}', digest)
        if match is None or match.group(1) not in repositories:
            return tags, 'PROTECTED_OR_UNPAIRED_DIGEST'
    return tags, 'UNREFERENCED_CANDIDATE_ONLY'


def cleanup(report=None):
    report = report if report is not None else {}
    report.update(schema='RC6_PRETRANSFER_IMAGE_CLEANUP_V1', status='RED', removed_tags=[])
    before = safety()
    free_before = shutil.disk_usage('/').free
    report['disk_before'] = free_before
    ids = sorted(set(docker('image', 'ls', '-aq', '--no-trunc').splitlines()))
    if len(ids) > 64 or any(IMAGE.fullmatch(image) is None for image in ids):
        raise Rejected('IMAGE_IDS_INVALID')
    # Complete inventory before any mutation; unknown inspection never means unused.
    inventory = [{'image_id': image, 'tags': tags, 'reason': reason}
                 for image in ids for tags, reason in [eligibility(image)]]
    report['inventory'] = inventory
    report['warnings'] = [
        {'image_id': item['image_id'], 'reason': item['reason']}
        for item in inventory
        if item['reason'] == 'OWNERSHIP_LABELS_UNVERIFIED'
    ]
    removed = report['removed_tags']
    deadline = time.monotonic() + 90
    for entry in inventory:
        if entry['reason'] != 'UNREFERENCED_CANDIDATE_ONLY':
            continue
        for tag in entry['tags']:
            if time.monotonic() >= deadline:
                raise Rejected('CLEANUP_DEADLINE')
            if safety() != before:
                raise Rejected('RUNTIME_CHANGED')
            tags, reason = eligibility(entry['image_id'])
            if reason != 'UNREFERENCED_CANDIDATE_ONLY' or tag not in tags:
                raise Rejected('IMAGE_OWNERSHIP_CHANGED')
            # Never force. Docker also rejects a new container reference racing
            # the final tag deletion. No IDs, volumes, stable or external tags.
            docker('image', 'rm', tag)
            removed.append(tag)
    if safety() != before:
        raise Rejected('RUNTIME_CHANGED')
    free_after = shutil.disk_usage('/').free
    report.update(status='GREEN', disk_after=free_after,
                  space_recovered=free_after - free_before, runtime_unchanged=True)
    return report


def main():
    report = {'schema': 'RC6_PRETRANSFER_IMAGE_CLEANUP_V1', 'status': 'RED'}
    try:
        with promotion_lock():
            cleanup(report)
    except (Rejected, subprocess.TimeoutExpired, OSError, ValueError, TypeError, sqlite3.Error) as exc:
        report.update(status='RED', reason=str(exc) if isinstance(exc, Rejected) else type(exc).__name__)
        if 'disk_before' in report:
            report['disk_after'] = shutil.disk_usage('/').free
            report['space_recovered'] = report['disk_after'] - report['disk_before']
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'GREEN' else 2


if __name__ == '__main__':
    raise SystemExit(main())

