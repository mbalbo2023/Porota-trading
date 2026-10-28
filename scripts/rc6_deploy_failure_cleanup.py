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

def owned_tags(sha):
    return ('porota-predeploy-v2:' + sha,
            'porota-trading-bot:17.0.0-rc6-candidate-' + sha)


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


def image_present(identity):
    ids = docker('image', 'ls', '-aq', '--no-trunc').splitlines()
    if len(ids) > 128 or any(IMAGE_ID.fullmatch(value) is None for value in ids):
        raise CleanupRejected('IMAGE_INVENTORY_INVALID')
    return identity in ids


def image_aliases(image, candidate_sha, *, allow_untagged=False):
    """Prove the entire image has only this candidate's aliases.

    A Docker tag may point to an image owned by another repository. Removing
    that tag would free no space and obscures the other owner's identity.
    """
    raw = docker('image', 'inspect', '--format',
                 '{{json .Id}}|{{json .RepoTags}}|{{json .RepoDigests}}', image)
    fields = raw.split('|')
    if len(fields) != 3:
        raise CleanupRejected('IMAGE_ALIASES_INVALID')
    try:
        identity, tags, digests = (json.loads(field) for field in fields)
    except (ValueError, TypeError) as exc:
        raise CleanupRejected('IMAGE_ALIASES_INVALID') from exc
    if (identity != image or not isinstance(tags, (list, type(None))) or
            not isinstance(digests, (list, type(None)))):
        raise CleanupRejected('IMAGE_ALIASES_INVALID')
    tags, digests = tags or [], digests or []
    if any(not isinstance(value, str) for value in tags + digests):
        raise CleanupRejected('IMAGE_ALIASES_INVALID')
    if ((not tags and not allow_untagged) or
            any(tag not in owned_tags(candidate_sha) for tag in tags)):
        return False
    # A RepoDigest names a repository, not this candidate/run/attempt. Even a
    # porota-trading-bot digest may belong to a different deploy's registry
    # reference. No frozen artifact binds a digest to this attempt, so retain
    # its image until separately proven; never delete it by image ID here.
    return not digests


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


def cleanup(candidate_sha, run_id=None, attempt=None):
    if not isinstance(candidate_sha, str) or SHA.fullmatch(candidate_sha) is None:
        raise CleanupRejected('CANDIDATE_SHA_INVALID')
    if run_id is None and attempt is None:
        # Legacy unit fixtures only.  The canonical workflow always supplies
        # both identities and can never erase another attempt's upload.
        staging = REMOTE_ROOT / ('porota-deploy-v2-' + candidate_sha)
    elif (isinstance(run_id, str) and isinstance(attempt, str) and
          re.fullmatch(r'[1-9][0-9]*', run_id) and
          re.fullmatch(r'[1-9][0-9]*', attempt)):
        staging = REMOTE_ROOT / f'porota-deploy-v2-{candidate_sha}-{run_id}-{attempt}'
    else:
        raise CleanupRejected('ATTEMPT_IDENTITY_INVALID')
    # Never follow a substituted staging root, even if no Docker mount uses it.
    if staging.is_symlink() or staging.resolve().parent != REMOTE_ROOT.resolve():
        raise CleanupRejected('STAGING_PATH_INVALID')
    disk_before = shutil.disk_usage('/').free
    removed, retained, errors = [], [], []
    removed_images = []
    candidate_images = set()
    retained_reasons = {}
    # Inspection failure is blocking: an unknown Docker owner is not "unused".
    container_inventory()
    for tag in owned_tags(candidate_sha):
        try:
            current_id = image_id(tag)
            if current_id is None:
                continue
            referenced, _ = container_inventory()
            if current_id in referenced or current_id == image_id(STABLE_IMAGE):
                retained.append(tag)
                retained_reasons[tag] = ('CONTAINER_REFERENCED' if current_id in referenced
                                         else 'STABLE_IMAGE_ALIAS')
                continue
            if not image_aliases(current_id, candidate_sha):
                retained.append(tag)
                retained_reasons[tag] = 'NONCANDIDATE_ALIAS_OR_DIGEST'
                continue
            candidate_images.add(current_id)
            # No force: Docker refuses deletion if a new owner references the
            # image between inspection and removal. Never remove a stable tag.
            referenced, _ = container_inventory()
            if current_id in referenced or not image_aliases(current_id, candidate_sha):
                raise CleanupRejected('IMAGE_OWNERSHIP_CHANGED')
            docker('image', 'rm', tag)
            removed.append(tag)
        except (CleanupRejected, subprocess.TimeoutExpired):
            errors.append('IMAGE_CLEANUP_FAILED')
    # A digest-only or dangling image may remain after removing both tags.
    # Reclaim only the ID obtained from this exact attempt's proven aliases.
    for candidate_image in sorted(candidate_images):
        try:
            if not image_present(candidate_image):
                continue
            referenced, _ = container_inventory()
            if (candidate_image in referenced or
                    candidate_image == image_id(STABLE_IMAGE) or
                    not image_aliases(candidate_image, candidate_sha,
                                      allow_untagged=True)):
                raise CleanupRejected('IMAGE_OWNERSHIP_CHANGED')
            docker('image', 'rm', candidate_image)
            removed_images.append(candidate_image)
        except (CleanupRejected, subprocess.TimeoutExpired):
            errors.append('IMAGE_ID_CLEANUP_FAILED')
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
    disk_after = shutil.disk_usage('/').free
    return {
        'schema': 'POROTA_DEPLOY_FAILURE_CLEANUP_V1',
        'candidate_sha': candidate_sha,
        'status': 'RED' if errors else 'GREEN',
        'removed_tags': removed, 'retained_tags': retained,
        'removed_image_ids': removed_images,
        'retained_reasons': retained_reasons,
        'staging_removed': not staging.exists(),
        'disk_before': disk_before, 'disk_after': disk_after,
        'space_recovered': disk_after - disk_before,
        'shared_cache_action': 'UNTOUCHED_NO_HOST_BUILD',
        'errors': errors,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate-sha', required=True)
    parser.add_argument('--run-id')
    parser.add_argument('--attempt')
    args = parser.parse_args(argv)
    host_disk_before = shutil.disk_usage('/').free
    try:
        with promotion_lock():
            report = cleanup(args.candidate_sha, args.run_id, args.attempt)
    except (CleanupRejected, subprocess.TimeoutExpired, OSError) as exc:
        report = {'schema': 'POROTA_DEPLOY_FAILURE_CLEANUP_V1', 'status': 'RED',
                  'candidate_sha': args.candidate_sha,
                  'errors': [str(exc) if isinstance(exc, CleanupRejected) else type(exc).__name__]}
    report['host_disk_before'] = host_disk_before
    report['host_disk_after'] = shutil.disk_usage('/').free
    report['host_space_recovered'] = report['host_disk_after'] - host_disk_before
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'GREEN' else 2


if __name__ == '__main__':
    raise SystemExit(main())
