import fcntl
import http.client
import json
import os
import re
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TARGETS = (
    ('sha256:429aa37da444e1121d61f9488c705ebde7eb3623fecbb2e0569bab2022464aa9',
     ('porota-predeploy-v2:dd22a1b0761cc4f231813edb6b502d1dd21bb8de',
      'porota-trading-bot:17.0.0-rc6-candidate-dd22a1b0761cc4f231813edb6b502d1dd21bb8de'),
     {'porota-predeploy-v2', 'porota-trading-bot'}, 2),
    ('sha256:5f75d0e7e8e4392e795fb2617c90915ba115c24570aa8643e175d2f5aa36d936',
     ('porota-predeploy-v2:dda9370a6cd3709425bc3f2faa77a38ee6a5c5d8',),
     {'porota-predeploy-v2'}, 1),
)
ACTIVE_IMAGES = {
    'porota_production_observer': 'sha256:90e54acb1189a03acbed4b9228943ab556bdbe3a01fd515285951fc90eda93e7',
    'porota_production_dashboard': 'sha256:90e54acb1189a03acbed4b9228943ab556bdbe3a01fd515285951fc90eda93e7',
    'porota_critical_approval_rc6': 'sha256:586464c20b74ab3a7ba842df8dfafcd76f21eb4c7bed553f23f6f187924ff735',
}
EXPECTED_CONTAINER_IDS = {
    'porota_production_observer': '6b66ba77e9fc',
    'porota_production_dashboard': 'a854c6930cdd',
    'porota_critical_approval_rc6': '0d566777f5dc',
}
LOCK = Path('/run/lock/porota-rc6-deploy-v2.lock')
BARRIER = LOCK.parent / 'porota-rc6-deploy-v2-attempt.json'

def cmd(args, timeout=18, allow_missing=False):
    p = subprocess.run(args, capture_output=True, text=True,
                       timeout=timeout, check=False)
    if p.returncode and allow_missing and (
            'No such image:' in p.stderr or 'No such object:' in p.stderr):
        return None
    if p.returncode:
        raise RuntimeError('DOCKER_COMMAND_RED:' + args[1])
    return p.stdout.strip()

def free():
    v = os.statvfs('/')
    return {'bytes': v.f_bavail * v.f_frsize,
            'free_inodes': v.f_favail}

def metadata(part):
    c = http.client.HTTPConnection('169.254.169.254', timeout=2)
    try:
        c.request('GET', '/metadata/v1/' + part)
        r = c.getresponse()
        if r.status != 200:
            raise RuntimeError('DROPLET_METADATA_RED')
        return r.read(128).decode('ascii').strip()
    finally:
        c.close()

def lock_existing(trusted_uid=0):
    d = os.lstat(LOCK.parent)
    if not stat.S_ISDIR(d.st_mode) or d.st_uid != trusted_uid:
        raise RuntimeError('MUTEX_PARENT_RED')
    fd = os.open(LOCK, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    s, p = os.fstat(fd), os.lstat(LOCK)
    if (not stat.S_ISREG(s.st_mode) or s.st_uid != trusted_uid or
        stat.S_IMODE(s.st_mode) != 0o600 or s.st_nlink != 1 or
        (s.st_dev, s.st_ino) != (p.st_dev, p.st_ino)):
        os.close(fd)
        raise RuntimeError('MUTEX_ALIAS_RED')
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        os.close(fd)
        raise RuntimeError('MUTEX_BUSY')
    if (os.lstat(LOCK).st_dev, os.lstat(LOCK).st_ino) != (s.st_dev, s.st_ino):
        os.close(fd)
        raise RuntimeError('MUTEX_REPLACED')
    if BARRIER.exists():
        bs = os.lstat(BARRIER)
        if (not stat.S_ISREG(bs.st_mode) or bs.st_uid != trusted_uid or
            stat.S_IMODE(bs.st_mode) != 0o600 or bs.st_size > 1024 or bs.st_nlink != 1):
            os.close(fd)
            raise RuntimeError('BARRIER_ALIAS_RED')
        with os.fdopen(os.open(BARRIER, os.O_RDONLY | os.O_NOFOLLOW), 'r') as f:
            barrier = json.load(f)
        if barrier.get('status') != 'IDLE':
            os.close(fd)
            raise RuntimeError('BARRIER_ACTIVE')
    return fd

def containers():
    ids = cmd(['docker', 'container', 'ls', '-aq', '--no-trunc'], timeout=6).splitlines()
    if len(ids) != 3:
        raise RuntimeError('GLOBAL_CONTAINER_COUNT_CHANGED')
    out = {}
    for cid in ids:
        raw = cmd(['docker', 'inspect', '--type=container', '--format',
                   '{{.Id}}|{{.Name}}|{{.Image}}|{{.State.Status}}|{{.RestartCount}}', cid], 5)
        parts = raw.split('|')
        if len(parts) != 5 or parts[0] != cid:
            raise RuntimeError('CONTAINER_INSPECT_RED')
        name = parts[1].lstrip('/')
        if (name not in ACTIVE_IMAGES or parts[2] != ACTIVE_IMAGES[name] or
            parts[3] != 'running' or not cid.startswith(EXPECTED_CONTAINER_IDS[name])):
            raise RuntimeError('ACTIVE_CONTAINER_IDENTITY_RED')
        out[name] = (parts[0], parts[2], parts[3], parts[4])
    if set(out) != set(ACTIVE_IMAGES):
        raise RuntimeError('ACTIVE_CONTAINER_SET_RED')
    return out

def image(iid):
    raw = cmd(['docker', 'image', 'inspect', '--format',
               '{{.Id}}|{{json .RepoTags}}|{{json .RepoDigests}}|{{json .RootFS.Layers}}', iid],
              timeout=6, allow_missing=True)
    if raw is None:
        return None
    parts = raw.split('|')
    if len(parts) != 4 or parts[0] != iid:
        raise RuntimeError('IMAGE_INSPECT_RED')
    tags, digests, layers = (json.loads(x) or [] for x in parts[1:])
    if any(not isinstance(x, list) for x in (tags, digests, layers)):
        raise RuntimeError('IMAGE_FORMAT_RED')
    return {'tags': tags, 'digest_repos': {d.split('@', 1)[0] for d in digests},
            'digest_count': len(digests), 'layers': tuple(layers)}

def protected():
    c = containers()
    layers = {iid: image(iid)['layers'] for iid in set(ACTIVE_IMAGES.values())}
    return c, layers

def target_guard(iid, remaining, allowed_repos, initial_digests, overlap_layers):
    if iid in {row[1] for row in containers().values()}:
        raise RuntimeError('TARGET_REFERENCED_BY_CONTAINER')
    x = image(iid)
    if (x is None or set(x['tags']) != set(remaining) or len(x['tags']) != len(remaining) or
        not x['digest_repos'].issubset(allowed_repos) or
        x['digest_count'] > initial_digests or
        len(set(x['layers']) & overlap_layers) != 5):
        raise RuntimeError('TARGET_ALIAS_OR_LAYER_DRIFT')
    return x

def reclaim_target(target, first_protected, overlap_layers, receipt):
    iid, tags, repos, digests = target
    remaining = list(tags)
    for tag in tags:
        if protected() != first_protected:
            raise RuntimeError('ACTIVE_RUNTIME_DRIFT_BEFORE_REMOVE')
        target_guard(iid, remaining, repos, digests, overlap_layers)
        # No --force or broad prune; referenced protected layers remain.
        cmd(['docker', 'image', 'rm', '--no-prune', tag], timeout=25)
        receipt['removed_tags'].append(tag)
        remaining.remove(tag)
        if protected() != first_protected:
            raise RuntimeError('ACTIVE_RUNTIME_DRIFT_AFTER_REMOVE')
        if remaining:
            target_guard(iid, remaining, repos, digests, overlap_layers)
        else:
            residual = image(iid)
            if residual is not None:
                # Docker may leave a digest-only alias after the last tag.
                # This exact ID is safe only if no new owner or alias exists.
                target_guard(iid, [], repos, digests, overlap_layers)
                cmd(['docker', 'image', 'rm', '--no-prune', iid], timeout=25)
                receipt['removed_image_ids'].append(iid)
                if protected() != first_protected or image(iid) is not None:
                    raise RuntimeError('TARGET_IMAGE_RETAINED_AFTER_ID_REMOVE')
        receipt['checks'].append({'target': iid, 'removed': tag,
                                  'free': free()})


def execute():
    now = datetime.now(timezone.utc)
    receipt = {'schema': 'RC6_A155_EXACT_ORPHAN_RECLAIM_V2',
               'at_utc': now.isoformat(),
               'at_art': now.astimezone(ZoneInfo('America/Argentina/Buenos_Aires')).isoformat(),
               'targets': [x[0] for x in TARGETS],
               'removed_tags': [], 'removed_image_ids': [], 'checks': [],
               'stage_deleted': False, 'ppi_watch_mutated': False,
               'mode': 'EXACT_DOCKER_TAGS_AND_RESIDUAL_ID_ONLY'}
    lockfd = None
    try:
        if (metadata('id'), metadata('region')) != ('594077619', 'nyc1'):
            raise RuntimeError('DROPLET_IDENTITY_RED')
        receipt['droplet_id'], receipt['region'] = '594077619', 'nyc1'
        lockfd = lock_existing()
        receipt['before'] = free()
        first_protected = protected()
        first_containers, first_layers = first_protected
        overlap_layers = set().union(*(set(x) for x in first_layers.values()))
        # Preflight BOTH targets before the first mutation.
        for iid, tags, repos, digests in TARGETS:
            target_guard(iid, tags, repos, digests, overlap_layers)
        receipt['preflight'] = 'GREEN'
        for target in TARGETS:
            reclaim_target(target, first_protected, overlap_layers, receipt)
        receipt['status'] = 'GREEN'
    except Exception as exc:
        receipt['status'] = 'RED'
        receipt['error'] = type(exc).__name__ + ':' + str(exc)
    finally:
        receipt['after'] = free()
        if 'before' in receipt:
            receipt['free_delta_bytes'] = receipt['after']['bytes'] - receipt['before']['bytes']
        if lockfd is not None:
            os.close(lockfd)
        print('RC6_A155_EXACT_ORPHAN_RECLAIM=' + json.dumps(receipt, sort_keys=True))
    return 0 if receipt['status'] == 'GREEN' else 2


if __name__ == '__main__':
    sys.exit(execute())
