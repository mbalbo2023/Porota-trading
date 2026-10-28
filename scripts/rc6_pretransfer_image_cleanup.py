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
LOCK = Path('/tmp/porota-rc6-deploy-v2.lock')
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
    try:
        fd = os.open(LOCK, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        # No live promoter can exist without the canonical lock. Do not create
        # a root-owned replacement that would break the SSH owner later.
        yield
        return
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Rejected('PROMOTION_LOCK_INVALID')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Rejected('PROMOTION_STILL_RUNNING') from exc
        yield
    finally:
        os.close(fd)


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
                   '{{json .Id}}|{{json .RepoTags}}|{{json .RepoDigests}}', image).split('|')
    if len(values) != 3:
        raise Rejected('IMAGE_INVENTORY_INVALID')
    identity, tags, digests = map(json.loads, values)
    if identity != image or not isinstance(tags, (list, type(None))) or not isinstance(digests, (list, type(None))):
        raise Rejected('IMAGE_INVENTORY_INVALID')
    if any(not isinstance(tag, str) for tag in (tags or []) + (digests or [])):
        raise Rejected('IMAGE_TAGS_INVALID')
    return tags or [], digests or []


def eligibility(image):
    tags, digests = inspect_image(image)
    refs = references()  # includes stopped containers, not just running ones
    if image in refs:
        return tags, 'CONTAINER_REFERENCED'
    if not tags or any(CANDIDATE.fullmatch(tag) is None for tag in tags):
        return tags, 'PROTECTED_OR_NONCANDIDATE_ALIAS'
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
