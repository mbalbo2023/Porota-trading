#!/usr/bin/env python3
"""Reclaim only this failed attempt's staging and unreferenced image tags.

Run through the canonical workflow's bounded SSH cleanup step. This deliberately
does not prune shared caches/images, restart services, or touch runtime data.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

REMOTE_ROOT = Path('/tmp')
STABLE_IMAGE = 'porota-trading-bot:17.0.0-rc6'
SHA = re.compile(r'[0-9a-f]{40}')
IMAGE_ID = re.compile(r'sha256:[0-9a-f]{64}')


class CleanupRejected(RuntimeError):
    pass


@contextmanager
def promotion_lock():
    # The promoter creates the shared lock as the SSH owner. Do not create a
    # root-owned replacement when transfer fails before promotion starts.
    try:
        fd = os.open(REMOTE_ROOT / 'porota-rc6-deploy-v2.lock', os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        yield
        return
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise CleanupRejected('PROMOTION_LOCK_INVALID')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CleanupRejected('PROMOTION_STILL_RUNNING') from exc
        yield
    finally:
        os.close(fd)


def docker(*args):
    result = subprocess.run(['docker', *args], check=False, text=True,
                            capture_output=True, timeout=20)
    if result.returncode:
        raise CleanupRejected('DOCKER_COMMAND_FAILED:' + args[0])
    return result.stdout.strip()


def image_id(tag):
    value = docker('image', 'ls', '--quiet', '--no-trunc', tag)
    if not value:
        return None
    if IMAGE_ID.fullmatch(value) is None:
        raise CleanupRejected('IMAGE_ID_INVALID')
    return value


def container_inventory():
    images, mounts = set(), []
    for cid in docker('ps', '-aq').splitlines():
        if re.fullmatch(r'[0-9a-f]{12,64}', cid) is None:
            raise CleanupRejected('CONTAINER_ID_INVALID')
        image, separator, raw_mounts = docker(
            'inspect', '--format', '{{.Image}}|{{json .Mounts}}', cid).partition('|')
        if not separator or IMAGE_ID.fullmatch(image) is None:
            raise CleanupRejected('CONTAINER_INSPECTION_INVALID')
        try:
            parsed = json.loads(raw_mounts)
        except (ValueError, TypeError) as exc:
            raise CleanupRejected('MOUNT_INSPECTION_INVALID') from exc
        if not isinstance(parsed, list):
            raise CleanupRejected('MOUNT_INSPECTION_INVALID')
        images.add(image)
        for mount in parsed:
            if not isinstance(mount, dict):
                raise CleanupRejected('MOUNT_INSPECTION_INVALID')
            if mount.get('Type') == 'tmpfs' and mount.get('Source') in (None, ''):
                continue
            if mount.get('Type') not in {'bind', 'volume'}:
                raise CleanupRejected('MOUNT_TYPE_INVALID')
            source = mount.get('Source')
            if not isinstance(source, str) or not source.startswith('/'):
                raise CleanupRejected('MOUNT_SOURCE_INVALID')
            mounts.append(Path(source).resolve())
    return images, mounts


def overlaps(left, right):
    return left == right or left in right.parents or right in left.parents


def cleanup(candidate_sha):
    if not isinstance(candidate_sha, str) or SHA.fullmatch(candidate_sha) is None:
        raise CleanupRejected('CANDIDATE_SHA_INVALID')
    staging = REMOTE_ROOT / ('porota-deploy-v2-' + candidate_sha)
    # Never follow a substituted staging root, even if no Docker mount uses it.
    if staging.is_symlink() or staging.resolve().parent != REMOTE_ROOT.resolve():
        raise CleanupRejected('STAGING_PATH_INVALID')
    disk_before = shutil.disk_usage(REMOTE_ROOT).free
    removed, retained, errors = [], [], []
    # Inspection failure is blocking: an unknown Docker owner is not "unused".
    container_inventory()
    for tag in ('porota-predeploy-v2:' + candidate_sha,
                'porota-trading-bot:17.0.0-rc6-candidate-' + candidate_sha):
        try:
            current_id = image_id(tag)
            if current_id is None:
                continue
            referenced, _ = container_inventory()
            if current_id in referenced or current_id == image_id(STABLE_IMAGE):
                retained.append(tag)
                continue
            # No force: Docker refuses deletion if a new owner references the
            # image between inspection and removal. Never remove a stable tag.
            docker('image', 'rm', tag)
            removed.append(tag)
        except (CleanupRejected, subprocess.TimeoutExpired):
            errors.append('IMAGE_CLEANUP_FAILED')
    try:
        _, mounts = container_inventory()
        if any(overlaps(staging.resolve(), mount) for mount in mounts):
            errors.append('STAGING_REFERENCED_BY_CONTAINER')
        elif staging.exists():
            if not staging.is_dir():
                raise CleanupRejected('STAGING_NOT_DIRECTORY')
            shutil.rmtree(staging)
    except (CleanupRejected, subprocess.TimeoutExpired, OSError):
        errors.append('STAGING_CLEANUP_FAILED')
    disk_after = shutil.disk_usage(REMOTE_ROOT).free
    return {
        'schema': 'POROTA_DEPLOY_FAILURE_CLEANUP_V1',
        'candidate_sha': candidate_sha,
        'status': 'RED' if errors else 'GREEN',
        'removed_tags': removed, 'retained_tags': retained,
        'staging_removed': not staging.exists(),
        'disk_before': disk_before, 'disk_after': disk_after,
        'space_recovered': disk_after - disk_before,
        'shared_cache_action': 'UNTOUCHED_NO_HOST_BUILD',
        'errors': errors,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate-sha', required=True)
    args = parser.parse_args(argv)
    try:
        with promotion_lock():
            report = cleanup(args.candidate_sha)
    except (CleanupRejected, subprocess.TimeoutExpired, OSError) as exc:
        report = {'schema': 'POROTA_DEPLOY_FAILURE_CLEANUP_V1', 'status': 'RED',
                  'candidate_sha': args.candidate_sha,
                  'errors': [str(exc) if isinstance(exc, CleanupRejected) else type(exc).__name__]}
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'GREEN' else 2


if __name__ == '__main__':
    raise SystemExit(main())
