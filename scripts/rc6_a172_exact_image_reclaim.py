#!/usr/bin/env python3
"""Remove only two historically proven, now orphaned RC6 image IDs.

Predeploys #40603564980/#40761278095 and Deploys #40607685153/#40765366297
bind candidate SHAs to the loaded Docker IDs. A172 RO #40774748082 established
zero all-container refs and the four-image baseline. This operation rechecks
all facts under the host mutex; no force, prune, volume, DB, backup, stage,
PPI Watch, service or timer mutation is permitted.
"""
from __future__ import annotations

import fcntl
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
ACTIVE = 'sha256:e61052af13c01d9ce76e92f38b50c794dac8a146427e132d43a8a80554a879aa'
CRITICAL = 'sha256:586464c20b74ab3a7ba842df8dfafcd76f21eb4c7bed553f23f6f187924ff735'
TARGETS = (
    ('2325eba329878c62a61cb47bb66d33ccf279bd4a',
     'sha256:90e54acb1189a03acbed4b9228943ab556bdbe3a01fd515285951fc90eda93e7'),
    ('33ea6d365c47d415fc5480a23b1338c208c2a29f',
     'sha256:c3af151c90dcf124d38843e4fb99219b01bfc9863cc1d7a96bdedfd419e6d98f'),
)
OWNED = {
    '9b7d41f04b2e7847318c6f8ded0440b9d6a030a5bbb99c3ac5f31c2815beaa95': ACTIVE,
    '3d51f57985d8375f6c06c9a57d8a7f6c6dd4b99ee03bdeb0efac440f92e43aa3': ACTIVE,
    '0d566777f5dca7342d0fbcb3ff538248445069d126064e88af963c3c8d023a21': CRITICAL,
}
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


def protected_state(expected_ids):
    read_barrier_absent()
    paper_mode()
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
    if STABLE not in tags or not exact_tag_set('4ce63a68c668cadd2a0aa7e8aded44ae4a3c46a6').issubset(tags):
        raise Rejected('ACTIVE_STABLE_TAG_CHANGED')
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
        for sha, iid in TARGETS:
            tags, digests, _ = image_record(iid)
            exact_target_aliases(sha, tags, digests, initial=True)
        report['disk_before'] = shutil.disk_usage('/').free
        for sha, iid in TARGETS:
            pre = shutil.disk_usage('/').free
            pending = exact_tag_set(sha)
            original_digests = set(image_record(iid)[1])
            for tag in sorted(pending):
                protected_state(expected_ids)
                tags, digests, _ = image_record(iid)
                exact_target_aliases(sha, tags, digests, initial=False)
                if set(tags) != pending:
                    raise Rejected('TARGET_TAG_SET_CHANGED')
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
            report['removed_image_ids'].append(iid)
            report['steps'].append({'candidate_sha': sha, 'image_id': iid,
                'disk_before': pre, 'disk_after': after, 'observed_delta_bytes': after - pre,
                'protected_containers_unchanged': True})
        final_free = shutil.disk_usage('/').free
        report.update(status='GREEN', disk_after=final_free,
                      observed_delta_bytes=final_free-report['disk_before'],
                      real_orders_sent=0, ppi_watch='NOT_ACCESSED')
        return report
    finally:
        os.close(fd)


def main():
    report = {'schema': 'RC6_A172_EXACT_IMAGE_RECLAIM_V1', 'status': 'RED'}
    try:
        reclaim(report)
    except (Rejected, OSError, ValueError, TypeError, UnicodeError,
            sqlite3.Error, subprocess.TimeoutExpired) as exc:
        report['reason'] = str(exc) if isinstance(exc, Rejected) else type(exc).__name__
        if 'disk_before' in report:
            report['disk_after'] = shutil.disk_usage('/').free
            report['observed_delta_bytes'] = report['disk_after'] - report['disk_before']
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'GREEN' else 2


if __name__ == '__main__':
    raise SystemExit(main())
