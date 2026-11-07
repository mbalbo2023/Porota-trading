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
    # The legacy /tmp lock is neither the real root-owned /run/lock mutex
    # nor an authorization for cleanup. Missing used to yield (fail open).
    # The delegated RO 2FD protocol is under separate review and is NOT
    # enabled until the wrapper and physical permissions are proved.
    raise CleanupRejected('PROMOTION_MUTEX_DELEGATED_PROOF_UNAVAILABLE')
    yield


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


def image_aliases(image, candidate_sha):
    """Prove the entire image has only this candidate's aliases.

    A Docker tag may point to an image owned by another repository. Removing
    that tag would free no space and obscures the other owner's identity.
    """
    raw = docker('image', 'inspect', '--format',
                 '{{json .Id}}|{{json .RepoTags}}|{{json .RepoDigests}}|{{json .Config.Labels}}', image)
    fields = raw.split('|')
    if len(fields) != 4:
        raise CleanupRejected('IMAGE_ALIASES_INVALID')
    try:
        identity, tags, digests, labels = (json.loads(field) for field in fields)
    except (ValueError, TypeError) as exc:
        raise CleanupRejected('IMAGE_ALIASES_INVALID') from exc
    if (identity != image or not isinstance(tags, (list, type(None))) or
            not isinstance(digests, (list, type(None)))):
        raise CleanupRejected('IMAGE_ALIASES_INVALID')
    tags, digests = tags or [], digests or []
    if (any(not isinstance(value, str) for value in tags + digests)
            or not isinstance(labels, dict)
            or any(not isinstance(key, str) or not isinstance(value, str)
                   for key, value in labels.items())):
        raise CleanupRejected('IMAGE_ALIASES_INVALID')
    if (not tags or any(tag not in owned_tags(candidate_sha) for tag in tags)
            or labels.get('porota.commit') != candidate_sha
            or labels.get('porota.predeploy') != 'v2'):
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


def host_mountpoints():
    """Read mountpoints in this namespace; Docker's bind inventory is not enough.

    The kernel escapes spaces, tabs, newlines, and backslashes as octal bytes.
    Reject malformed records instead of treating them as an empty inventory.
    """
    try:
        raw = Path('/proc/self/mountinfo').read_text(
            encoding='utf-8', errors='surrogateescape')
    except OSError as exc:
        raise CleanupRejected('HOST_MOUNT_INVENTORY_FAILED') from exc
    points = []
    for line in raw.splitlines():
        prefix, separator, _ = line.partition(' - ')
        fields = prefix.split()
        if (not separator or len(fields) < 6 or
                re.fullmatch(r'(?:[^\\]|\\[0-7]{3})*', fields[4]) is None):
            raise CleanupRejected('HOST_MOUNT_INVENTORY_INVALID')
        mountpoint = re.sub(r'\\([0-7]{3})',
                            lambda match: chr(int(match.group(1), 8)), fields[4])
        if not mountpoint.startswith('/') or '\x00' in mountpoint:
            raise CleanupRejected('HOST_MOUNT_INVENTORY_INVALID')
        points.append(Path(os.path.normpath(mountpoint)))
    if not points:
        raise CleanupRejected('HOST_MOUNT_INVENTORY_INVALID')
    return points


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
    # Shared-host fallback: there is no independent root-issued receipt
    # attesting ownership of the current exact attempt. In particular, the
    # path name or a DO_USER-written receipt is forgeable by another app with
    # the same UID. Before provenance is solved, do not call Docker or delete
    # anything, even when the candidate's tag names appear to match.
    disk_before = shutil.disk_usage('/').free
    disk_after = shutil.disk_usage('/').free
    return {
        'schema': 'POROTA_DEPLOY_FAILURE_CLEANUP_V1',
        'candidate_sha': candidate_sha,
        'status': 'RED',
        'removed_tags': [], 'retained_tags': [],
        'removed_image_ids': [],
        'retained_reasons': {},
        'staging_removed': False,
        'disk_before': disk_before, 'disk_after': disk_after,
        'space_recovered': 0,
        'shared_cache_action': 'UNTOUCHED_NO_HOST_BUILD',
        'errors': ['STAGING_CLEANUP_DEFERRED_UNVERIFIED'],
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
    # This fail-closed helper performed no deletion. Other host activity can
    # change disk free bytes while it exits; never attribute that to Porota.
    report['host_space_recovered'] = 0
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'GREEN' else 2


if __name__ == '__main__':
    raise SystemExit(main())
