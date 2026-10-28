#!/usr/bin/env python3
"""Reconcile only the stopped A143 pair from Deploy #40755524824 attempt 2.

The GitHub runner supplies hash-bound candidate sources. Probe mode takes the
existing flock without writing; reconcile mode runs inside the canonical
host mutex's exact cleanup_final phase and leaves both containers stopped.
"""
import base64
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import types
import urllib.request
import zlib

CANDIDATE = 'b778c8063e96574ddf859907abb99fae6f4e3484'
RUN = '40755524824'
ATTEMPT = '2'
LOCK = Path('/run/lock/porota-rc6-deploy-v2.lock')
BARRIER = Path('/run/lock/porota-rc6-deploy-v2-attempt.json')
ROOT = Path('/opt/porota-trading')
Q_SHA = '11e482553e0989113f22aa6fc022736d9b123993eb70baf16ce08edd091015fb'
S_SHA = 'edc7b597f47cbdb70224eaa4371a03069d447b5d80e0098829e43a877ce7d826'
OLD_HOST_Q_SHA = '572cbbd83d6c088f5c8dcfbc05e0147942fa15e5b41ca3483c42457ca026bbf8'
EXPECTED_DROPLET = {'id': '594077619', 'region': 'nyc1'}


def droplet_identity():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    observed = {}
    for key in ('id', 'region'):
        with opener.open('http://169.254.169.254/metadata/v1/' + key,
                         timeout=1.5) as response:
            if response.status != 200:
                raise ValueError('A169_METADATA_STATUS_RED')
            observed[key] = response.read(64).decode('ascii').strip()
    if observed != EXPECTED_DROPLET:
        raise ValueError('A169_DROPLET_IDENTITY_RED')
    return observed


def existing_flock():
    fd = os.open(LOCK, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    info = os.fstat(fd)
    path = LOCK.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or
            stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or
            (info.st_dev, info.st_ino) != (path.st_dev, path.st_ino)):
        os.close(fd)
        raise ValueError('A169_EXISTING_MUTEX_IDENTITY_RED')
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BaseException:
        os.close(fd)
        raise


def barrier_status(mode, trusted_uid=0):
    if mode == 'post':
        if BARRIER.exists() or BARRIER.is_symlink():
            raise ValueError('A169_BARRIER_NOT_RETIRED')
        return None
    if BARRIER.is_symlink() or not BARRIER.is_file():
        raise ValueError('A169_BARRIER_PATH_RED')
    info = BARRIER.stat()
    if (info.st_uid != trusted_uid or stat.S_IMODE(info.st_mode) != 0o600 or
            info.st_nlink != 1 or not 0 < info.st_size < 1024):
        raise ValueError('A169_BARRIER_IDENTITY_RED')
    row = json.loads(BARRIER.read_bytes())
    expected = {'candidate': CANDIDATE, 'run': RUN, 'attempt': ATTEMPT,
                'phase': 'cleanup_payload' if mode == 'probe' else 'cleanup_final',
                'status': 'IDLE' if mode == 'probe' else 'INFLIGHT'}
    if row != expected:
        raise ValueError('A169_BARRIER_NOT_EXACT_ATTEMPT')
    return row


def staged(encoded, digest, name, max_packed, max_source):
    if (not isinstance(encoded, str) or len(encoded) > 180000 or
            not re.fullmatch('[0-9a-f]{64}', digest)):
        raise ValueError('A169_STAGED_INPUT_RED')
    packed = base64.b64decode(encoded, validate=True)
    if not 0 < len(packed) < max_packed:
        raise ValueError('A169_STAGED_PACKED_RED')
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    source = inflater.decompress(packed, max_source)
    if (not 20000 < len(source) < max_source or not inflater.eof or
            inflater.unconsumed_tail or inflater.unused_data or
            hashlib.sha256(source).hexdigest() != digest):
        raise ValueError('A169_STAGED_HASH_OR_BOUNDS_RED')
    module = types.ModuleType(name)
    exec(compile(source, 'candidate/' + name + '.py', 'exec'), module.__dict__)
    return module


def exact_receipt(q, mode):
    path = ROOT / 'data/deploy' / ('quiescence-' + CANDIDATE + '.json')
    if path.is_symlink() or not path.is_file():
        raise ValueError('A169_RECEIPT_PATH_RED')
    info = path.stat()
    if info.st_nlink != 1 or not 0 < info.st_size < 65536:
        raise ValueError('A169_RECEIPT_BOUNDS_RED')
    raw = path.read_bytes()
    row = json.loads(raw)
    if (row.get('candidate_sha') != CANDIDATE or
            row.get('status') != ('PRIOR_A143_UNVALIDATED_HELD_STOPPED'
                                  if mode == 'post' else 'RECOVERY_UNVERIFIED') or
            row.get('installation_started') is not False or
            row.get('a143_pending_adopted') is not True or
            row.get('candidate_image_intent') is not None or
            row.get('real_orders_sent') != 0 or
            row.get('ppi_watch_accessed') is not False or
            set(row.get('containers', {})) != set(q.OWNED) or
            set(row.get('stop_evidence', {})) != set(q.OWNED)):
        raise ValueError('A169_RECEIPT_NOT_PREINSTALL_FAILURE')
    if mode == 'post':
        admin = row.get('administrative_reconciliation', {})
        if (admin.get('schema') != 'rc6.a169.exact-held-stop.v1' or
                admin.get('failure_signature') != 'GZIP_STAGED_MTIME_OUT_OF_RANGE' or
                admin.get('host_attempt') != [CANDIDATE, RUN, ATTEMPT] or
                not re.fullmatch('[0-9a-f]{64}', admin.get('prior_receipt_sha256', ''))):
            raise ValueError('A169_ADMIN_RECEIPT_CHANGED')
    return path, row, hashlib.sha256(raw).hexdigest()


def prove(q, operator, mode):
    installed = ROOT / 'scripts/rc6_deploy_quiescence.py'
    if (installed.is_symlink() or not installed.is_file() or
            hashlib.sha256(installed.read_bytes()).hexdigest() != OLD_HOST_Q_SHA):
        raise ValueError('A169_INSTALLED_751_SOURCE_CHANGED')
    path, receipt, before = exact_receipt(q, mode)
    # The preceding cleanup lost its staged A125 callback and persisted RED.
    # Change only the in-memory status to invoke the existing exact stop proof.
    book = q.a143_stopped_continuation(
        dict(receipt, status=('DRAINED' if mode != 'post' else
                             'PRIOR_A143_UNVALIDATED_HELD_STOPPED')),
        operator_a151_proof=operator.exact_a151_operator_preopen_pause,
        operator_timer_disabled=operator.operator_preopen_timer_disabled)
    if (book.get('real_orders_sent') != 0 or
            book.get('source_sha') != q.A143_PENDING_SOURCE_SHA or
            (book.get('process_state'), book.get('session_state')) not in
            {('STOPPED', 'BOOT_CALENDAR'), ('DEGRADED', 'BOOT_CALENDAR')}):
        raise ValueError('A169_STOPPED_PAPER_BOOK_RED')
    return path, receipt, before, book


def held_timers(operator):
    path = operator.ROOT / ('scheduler-suspend-' + CANDIDATE + '.json')
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError('A169_SUSPENSION_RECEIPT_UNSAFE')
    row = json.loads(path.read_bytes())
    names = row.get('timers')
    if (row.get('candidate_sha') != CANDIDATE or row.get('status') != 'SUSPENDED' or
            type(names) is not list or len(names) != 16 or
            len(set(names)) != 16 or set(row.get('attempted', [])) != set(names) or
            row.get('preserved_iol_timer') != operator.IOL_OAUTH_GUARD_TIMER or
            row.get('ppi_before') != operator.ppi_snapshot()):
        raise ValueError('A169_SUSPENSION_IDENTITY_RED')
    for name in names:
        expected = (('active', 'enabled') if name == operator.IOL_OAUTH_GUARD_TIMER
                    else ('inactive', 'disabled') if name == operator.OPERATOR_PREOPEN_TIMER
                    else ('inactive', 'enabled'))
        current = (operator.command('systemctl', 'is-active', name)[0],
                   operator.command('systemctl', 'is-enabled', name)[0])
        service, rc = operator.command('systemctl', 'show', name, '-p', 'Unit', '--value')
        if current != expected or rc or service != name.removesuffix('.timer') + '.service':
            raise ValueError('A169_TIMER_OR_SERVICE_CHANGED')
        if name != operator.IOL_OAUTH_GUARD_TIMER and operator.command(
                'systemctl', 'is-active', service)[0] in {
                    'active', 'activating', 'reloading', 'deactivating'}:
            raise ValueError('A169_WORKER_ACTIVE')
    return len(names)


def run(mode, q, operator):
    if mode not in {'probe', 'reconcile', 'post'} or os.geteuid() != 0:
        raise ValueError('A169_MODE_OR_ROOT_RED')
    fd = existing_flock() if mode in {'probe', 'post'} else None
    try:
        droplet = droplet_identity()
        barrier_status(mode)
        path, receipt, before, book = prove(q, operator, mode)
        count = held_timers(operator)
        if mode == 'reconcile':
            receipt['status'] = 'PRIOR_A143_UNVALIDATED_HELD_STOPPED'
            receipt['runtime_health'] = 'PRIOR_A143_PENDING_NOT_GO'
            receipt['administrative_reconciliation'] = {
                'schema': 'rc6.a169.exact-held-stop.v1',
                'failure_signature': 'GZIP_STAGED_MTIME_OUT_OF_RANGE',
                'prior_receipt_sha256': before,
                'host_attempt': [CANDIDATE, RUN, ATTEMPT],
                'at_utc': datetime.now(timezone.utc).isoformat()}
            q.persist(path, receipt)
            q.a143_stopped_continuation(
                receipt,
                operator_a151_proof=operator.exact_a151_operator_preopen_pause,
                operator_timer_disabled=operator.operator_preopen_timer_disabled)
        return {'schema':'rc6.a169.exact-held-stop.v1',
                'status':{'probe':'PROVEN','reconcile':'RECONCILED',
                          'post':'POST_PROVEN'}[mode],
                'candidate':CANDIDATE,'run':RUN,'attempt':ATTEMPT,
                'droplet':droplet,
                'receipt_before_sha256':before,
                'old_image_id':q.A143_PENDING_IMAGE_ID,
                'paper_state':[book['process_state'],book['session_state']],
                'real_orders_sent':0,'ppi_watch_accessed':False,
                'containers':'BOTH_EXACT_STOPPED','operator_hold':'EXACT_DISABLED',
                'timers_held':count,'ppi_watch':'STATE_ONLY',
                'disk_free_bytes':shutil.disk_usage('/').free,
                'inode_free_percent':round(100 * os.statvfs('/').f_ffree /
                                           os.statvfs('/').f_files, 6),
                'host_mutations':0 if mode in {'probe', 'post'} else 1}
    finally:
        if fd is not None:
            os.close(fd)


if __name__ == '__main__':
    try:
        if len(sys.argv) != 6:
            raise ValueError('A169_ARGUMENT_COUNT_RED')
        mode, q_b64, s_b64, q_sha, s_sha = sys.argv[1:]
        if (q_sha != Q_SHA or s_sha != S_SHA):
            raise ValueError('A169_CANDIDATE_SOURCE_HASH_RED')
        q = staged(q_b64, q_sha, 'rc6_a169_quiescence', 110000, 190000)
        operator = staged(s_b64, s_sha, 'rc6_a169_operator', 30000, 110000)
        result = run(mode, q, operator)
        print('RC6_A169_HELD_STOP='+json.dumps(result,sort_keys=True,separators=(',',':')))
    except Exception as exc:
        message=str(exc).split(':',1)[0]
        code=message if re.fullmatch('[A-Z][A-Z0-9_]{2,99}',message) else type(exc).__name__.upper()
        print('RC6_A169_HELD_STOP=RED|'+code)
        raise SystemExit(2)
