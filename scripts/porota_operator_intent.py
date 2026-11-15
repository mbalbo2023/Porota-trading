#!/usr/bin/env python3
"""Preserve PAPER operator intent in the private rootless configuration.

The legacy root-owned decision is copied exactly once, without granting new
consent. The configuration is mounted read-only outside writable runtime DATA.
Subsequent Actions intent changes use this location exclusively.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import sys

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from scripts import rc6_operator_paper_rearm as legacy


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def directory(home):
    return Path(home) / '.config/porota-rc6/operator'


def private_directory(path, *, create=True):
    path = Path(path).absolute()
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'INTENT_PATH_ALIAS')
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    meta = path.stat()
    require(stat.S_ISDIR(meta.st_mode) and meta.st_uid == os.getuid()
            and stat.S_IMODE(meta.st_mode) == 0o700, 'INTENT_DIRECTORY_UNSAFE')
    return path


def private_read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        meta = os.fstat(fd)
        require(stat.S_ISREG(meta.st_mode) and meta.st_uid == os.getuid()
                and meta.st_nlink == 1 and stat.S_IMODE(meta.st_mode) == 0o600
                and 0 < meta.st_size <= legacy.MAX_JSON_BYTES, 'INTENT_FILE_UNSAFE')
        raw = os.read(fd, legacy.MAX_JSON_BYTES + 1)
        require(len(raw) == meta.st_size, 'INTENT_FILE_CHANGED')
        return raw
    finally:
        os.close(fd)


def atomic_write(path, raw):
    fd, temporary = tempfile.mkstemp(prefix='.intent-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_intent(home, *, now=None):
    try:
        target = private_directory(directory(home), create=False) / legacy.RECEIPT_NAME
    except FileNotFoundError:
        return None
    if not os.path.lexists(target):
        return None
    value = legacy._strict_object(private_read(target))
    legacy._validated_intent(value, now or datetime.now(legacy.TZ))
    return value


def migrate_intent(home, *, source=legacy.OPERATOR_DIR_HOST, now=None):
    now = now or datetime.now(legacy.TZ)
    target = private_directory(directory(home))
    marker = target / 'migration.json'
    if os.path.lexists(marker):
        migrated = legacy._strict_object(private_read(marker))
        require(migrated.get('schema') == 'porota.operator-migration.v1', 'INTENT_MIGRATION_INVALID')
        # Never replay an old ACTIVE over a more recent PAUSE.
        read_intent(home, now=now)
        return {'status': 'ALREADY_MIGRATED', 'source_sha256': migrated.get('source_sha256')}
    copied = target / legacy.RECEIPT_NAME
    original = Path(source) / legacy.RECEIPT_NAME
    require(not os.path.lexists(copied) or os.path.lexists(original), 'EXISTING_INTENT_UNPROVEN')
    digest = None
    status = 'ABSENT'
    if os.path.lexists(original):
        # Readable 0644 legacy record: validate its original root custody even
        # though the migration itself runs as the existing unprivileged user.
        value, raw = legacy._root_owned_receipt(source, with_raw=True)
        status = legacy._validated_intent(value, now)
        digest = hashlib.sha256(raw).hexdigest()
        if os.path.lexists(copied):
            # A disconnect between the copy and its marker is recoverable only
            # when the private bytes still match the validated original.
            require(private_read(copied) == raw, 'EXISTING_INTENT_UNPROVEN')
        else:
            atomic_write(copied, raw)
    result = {'schema': 'porota.operator-migration.v1', 'status': status,
              'source_sha256': digest, 'migrated_at': now.isoformat()}
    atomic_write(marker, (json.dumps(result, sort_keys=True) + '\n').encode())
    return result


def change_intent(home, *, action, candidate_sha, source_tree, comment_id, run_id,
                  root=Path('/opt/porota-trading'), runtime_run=None, runtime_attempt=None):
    """Called after Actions authenticates the owner's explicit #624 comment."""
    require(action in {'pause', 'resume'}, 'INTENT_ACTION_INVALID')
    # This helper owns only private rootless intent. Never let a missing
    # caller environment select the legacy release proof in writable DATA.
    require(action == 'pause' or os.environ.get('POROTA_RUNTIME_BACKEND') == 'ROOTLESS',
            'ROOTLESS_INTENT_REQUIRED')
    require(isinstance(candidate_sha, str) and isinstance(source_tree, str)
            and legacy.SHA.fullmatch(candidate_sha) and legacy.SHA.fullmatch(source_tree), 'INTENT_SOURCE_INVALID')
    require(all(type(n) is int and n > 0 for n in (comment_id, run_id)), 'INTENT_PROVENANCE_INVALID')
    now = datetime.now(legacy.TZ)
    target = private_directory(directory(home))
    require((target / 'migration.json').is_file(), 'INTENT_MIGRATION_REQUIRED')
    previous = read_intent(home, now=now)
    if previous:
        desired = legacy.PAUSE_STATUS if action == 'pause' else legacy.REARM_STATUS
        if (previous['status'] == desired and previous['operator_comment_id'] == comment_id
                and previous['candidate_sha'] == candidate_sha and previous['candidate_tree_sha'] == source_tree
                and run_id >= previous['rearm_run_id']):
            return {'status': desired, 'operator_comment_id': comment_id,
                    'run_id': previous['rearm_run_id'], 'idempotent': True}
        legacy._require_new_owner_action(previous, comment_id, run_id)
    value = {'schema': legacy.REARM_SCHEMA,
             'status': legacy.PAUSE_STATUS if action == 'pause' else legacy.REARM_STATUS,
             'candidate_sha': candidate_sha, 'candidate_tree_sha': source_tree,
             'operator_comment_id': comment_id, 'operator_github_login': 'mbalbo2023',
             'rearm_run_id': run_id, 'issued_at_ar': now.isoformat(timespec='seconds')}
    if action == 'resume':
        require(all(type(n) is int and n > 0 for n in (runtime_run, runtime_attempt)), 'INTENT_RUNTIME_RUN_INVALID')
        _, _, image_id = legacy._validated_paper_release(
            marker_path=Path(root) / 'data/deploy/CURRENT_STATE_V2.json',
            frozen_path=Path(root) / 'data/deploy/porota-frozen-candidate.json', context='host',
            candidate_sha=candidate_sha, candidate_tree_sha=source_tree)
        value.update(image_id=image_id, deploy_run_id=runtime_run,
                     deploy_run_attempt=runtime_attempt, runtime_validation_run_id=runtime_run,
                     paper_mode='PRODUCTION_PAPER', real_orders_sent=0,
                     real_order_routes='NOT_CALLED', ppi_watch_untouched=True)
    legacy._validated_intent(value, now)
    if previous:
        raw = private_read(target / legacy.RECEIPT_NAME)
        archive = private_directory(target / 'history') / (hashlib.sha256(raw).hexdigest() + '.json')
        if not archive.exists():
            atomic_write(archive, raw)
    atomic_write(target / legacy.RECEIPT_NAME, (json.dumps(value, sort_keys=True) + '\n').encode())
    require(read_intent(home, now=now) == value, 'INTENT_WRITE_VERIFY_FAILED')
    return {'status': value['status'], 'operator_comment_id': comment_id, 'run_id': run_id}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('migrate', 'pause', 'resume'))
    parser.add_argument('--candidate-sha')
    parser.add_argument('--source-tree')
    parser.add_argument('--comment-id', type=int)
    parser.add_argument('--run-id', type=int)
    parser.add_argument('--runtime-run', type=int)
    parser.add_argument('--runtime-attempt', type=int)
    args = parser.parse_args()
    os.environ['POROTA_RUNTIME_BACKEND'] = 'ROOTLESS'
    from scripts.porota_rootless_host import Identity, Hold
    try:
        home = Identity.current().home
    except Hold as exc:
        raise ValueError(str(exc)) from exc
    if args.action == 'migrate':
        result = migrate_intent(home)
    else:
        result = change_intent(home, action=args.action, candidate_sha=args.candidate_sha,
                               source_tree=args.source_tree, comment_id=args.comment_id, run_id=args.run_id,
                               runtime_run=args.runtime_run, runtime_attempt=args.runtime_attempt)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as exc:
        print(json.dumps({'status': 'BLOQUEADO', 'reason': str(exc) if isinstance(exc, ValueError) else type(exc).__name__}))
        raise SystemExit(1)
