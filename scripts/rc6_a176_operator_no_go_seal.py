#!/usr/bin/env python3
"""One-shot, host-mutex-bound 29-Oct A176 NO_GO receipt, PAPER/0 only.

The only permitted host mutation is creating a previously absent, root-owned
data/deploy/operator-no-go-continuation-20261029.json. No timer, Docker, DB,
provider, PPI Watch or PAPER execution mutation is performed here.
"""
from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import tempfile
import urllib.request
from zoneinfo import ZoneInfo


ROOT = Path('/opt/porota-trading')
DIR = ROOT / 'data/deploy'
RECEIPT = DIR / 'operator-no-go-continuation-20261029.json'
LOCK = Path('/run/lock/porota-rc6-deploy-v2.lock')
BARRIER = Path('/run/lock/porota-rc6-deploy-v2-attempt.json')
DAY = '2026-10-29'
A175 = '18ba517a846c6798f0d12f39072465fc02fa03b5'
DEPLOY = '3c5210fef9d4f1d4c95b286b0dbc61b6654fe055'
IMAGE = 'sha256:9841cba8df3345fa22e39457645ba250431ad1f465ce4204d92f73f7816215b9'
SHA = {
    'a151': 'da5dfe9469fd6ff64e850b09e9cf94a1653f762aef8904d0d77fb9f241af69e2',
    'q': '5ddf0d5e340f7cac96bcbb339eeaeea3ed4aad635a2201fd55fffe375c961022',
    'scheduler': '64a8afc3591c42d4e58f3acb34905043c0ffe323b695ecf3337f99b66d6b0b6e',
    'marker': '42055f5225def0560453b2c40b5e4695082fa3235fc42a4596210cf0c1b8b161',
    'frozen': '1067182c282f17440850e50c7f8d347d84cfbb90090d7e71066483beb19450e0',
}
OWNED = {
    'porota_production_observer': ('72ed632d0514ec1835e3dcb7983aa2b13cfbcf2b46c5a62132e5c739f7df86eb', IMAGE),
    'porota_production_dashboard': ('9b01cf17b7c3e46d96474d998840fc0e4c4c4f828dd825bb44744710b99686bb', IMAGE),
    'porota_critical_approval_rc6': ('0d566777f5dca7342d0fbcb3ff538248445069d126064e88af963c3c8d023a21',
                                    'sha256:586464c20b74ab3a7ba842df8dfafcd76f21eb4c7bed553f23f6f187924ff735'),
}
PREOPEN = 'porota-preopen-rc6.timer'
GUARDIAN = 'porota-iol-oauth-health-rc6.timer'


class Rejected(RuntimeError):
    pass


def root_owned(st):
    """Host ownership guard; tests replace this probe, never the host policy."""
    return st.st_uid == 0


def reject_if(condition, code):
    if condition:
        raise Rejected(code)


def unique_pairs(pairs):
    out = {}
    for key, value in pairs:
        reject_if(key in out, 'JSON_DUPLICATE_KEY')
        out[key] = value
    return out


def read_guarded(path, digest=None, max_bytes=65536):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        reject_if(not stat.S_ISREG(st.st_mode) or not root_owned(st) or
                  st.st_nlink != 1 or st.st_size < 2 or st.st_size > max_bytes or
                  stat.S_IMODE(st.st_mode) & 0o022, 'RECEIPT_FILE_UNSAFE')
        raw = os.read(fd, max_bytes + 1)
        observed_digest = hashlib.sha256(raw).hexdigest()
        reject_if(len(raw) != st.st_size or
                  (digest is not None and observed_digest != digest),
                  'RECEIPT_DIGEST_CHANGED')
    finally:
        os.close(fd)
    row = json.loads(raw, object_pairs_hook=unique_pairs,
                     parse_constant=lambda _ : (_ for _ in ()).throw(Rejected('JSON_NONFINITE')))
    reject_if(type(row) is not dict, 'RECEIPT_SCHEMA_UNSAFE')
    return row, observed_digest


def read_exact(path, digest, max_bytes=65536):
    return read_guarded(path, digest, max_bytes)[0]


def inspect_host():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    values = []
    for name in ('id', 'region'):
        with opener.open('http://169.254.169.254/metadata/v1/' + name, timeout=3) as reply:
            reject_if(reply.status != 200, 'METADATA_HTTP_RED')
            values.append(reply.read(65).decode('ascii').strip())
    reject_if(tuple(values) != ('594077619', 'nyc1'), 'DROPLET_IDENTITY_CHANGED')


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=15)
    reject_if(len(result.stdout) > 262144, 'READ_ONLY_OUTPUT_TOO_LARGE')
    return result.stdout.strip(), result.returncode


def timer_state(name):
    active, active_rc = command('systemctl', 'is-active', name)
    enabled, enabled_rc = command('systemctl', 'is-enabled', name)
    reject_if((active, active_rc) not in {('active', 0), ('inactive', 3)} or
              (enabled, enabled_rc) not in {('enabled', 0), ('disabled', 1)},
              'TIMER_STATE_UNKNOWN')
    return {'active': active, 'enabled': enabled}


def ppi_snapshot():
    units, rc = command('systemctl', 'list-unit-files', '--no-legend')
    reject_if(rc != 0, 'PPI_UNIT_INVENTORY_UNAVAILABLE')
    names = sorted(line.split()[0] for line in units.splitlines()
                   if {'ppi', 'watch'} <= set(re.split(r'[-_.@]', line.split()[0].lower())))
    return [[name, command('systemctl', 'is-enabled', name)[0],
             command('systemctl', 'is-active', name)[0]] for name in names]


def paper_zero_and_flat():
    db_path = ROOT / 'data/paper_v17/observer_v17.db'
    with sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True, timeout=.3) as db:
        db.execute('PRAGMA query_only=ON')
        row = db.execute('SELECT mode,real_orders_sent FROM observer_state WHERE id=1').fetchone()
        reject_if(row != ('PRODUCTION_PAPER', 0) or
                  row is None or type(row[1]) is not int,
                  'PAPER_ZERO_REQUIRED')
        for name, status in (('paper_positions', 'OPEN'), ('paper_cauciones', 'OPEN'),
                             ('paper_future_positions', 'ACTIVE')):
            reject_if(db.execute('SELECT 1 FROM ' + name + ' WHERE status=? LIMIT 1',
                                (status,)).fetchone() is not None,
                      'PAPER_BOOK_NOT_FLAT')


def running_containers():
    containers, rc = command('docker', 'ps', '-aq', '--no-trunc')
    reject_if(rc != 0 or len(containers.splitlines()) != 3, 'CONTAINER_SET_CHANGED')
    expected_ids = {row[0] for row in OWNED.values()}
    reject_if(set(containers.splitlines()) != expected_ids, 'CONTAINER_SET_CHANGED')
    for name, (expected_id, expected_image) in OWNED.items():
        raw, rc = command('docker', 'inspect', '--format',
                          '{{json .Id}}|{{json .Image}}|{{json .State.Status}}|{{json .State.Running}}', name)
        reject_if(rc != 0 or len(raw.split('|')) != 4, 'CONTAINER_INSPECT_FAILED')
        cid, image, status, running = map(json.loads, raw.split('|'))
        reject_if((cid, image, status, running) !=
                  (expected_id, expected_image, 'running', True),
                  'CONTAINER_IDENTITY_CHANGED')


def validate_origin():
    a151 = read_exact(DIR / 'operator-preopen-pause-20261028.json', SHA['a151'])
    q = read_exact(DIR / ('quiescence-' + A175 + '.json'), SHA['q'])
    scheduler = read_exact(DIR / ('scheduler-suspend-' + A175 + '.json'), SHA['scheduler'])
    marker = read_exact(DIR / 'CURRENT_STATE_V2.json', SHA['marker'])
    frozen = read_exact(DIR / 'porota-frozen-candidate.json', SHA['frozen'], 16384)
    checks = (
        (a151, {'schema':'rc6.operator-preopen-pause.v2', 'status':'PAUSED_NO_GO',
                'operation_id':'A151-40686116017-1', 'trade_day_ar':'2026-10-28',
                'source_sha':'2325eba329878c62a61cb47bb66d33ccf279bd4a'}),
        (q, {'candidate_sha':A175, 'status':'FIX_FORWARD_VALIDATION_REQUIRED',
             'installation_started':True, 'ppi_watch_accessed':False,
             'real_orders_sent':0}),
        (scheduler, {'candidate_sha':A175, 'status':'SUSPENDED'}),
        (marker, {'candidate_sha':A175, 'deploy_sha':DEPLOY, 'image_id':IMAGE,
                  'validation_status':'DEPLOYED_VALIDATION_PENDING',
                  'mode':'PRODUCTION_PAPER', 'real_orders_sent':0,
                  'real_order_routes':'NOT_CALLED', 'ppi_watch_untouched':True}),
        (frozen, {'candidate_sha':A175, 'candidate_tree_sha':
                  '66c67bff66dd9f036cd7aa8de5ba312e04e53be6'}),
    )
    for row, fields in checks:
        reject_if(any(row.get(k) != v for k, v in fields.items()),
                  'HISTORICAL_RECEIPT_FIELDS_CHANGED')
    policy = ROOT / 'ops/policy/host-control-plane-reconciliation-v2.json'
    raw = policy.read_bytes()
    reject_if(hashlib.sha256(raw).hexdigest() != scheduler.get('policy_sha256') or
              not isinstance(scheduler.get('timers'), list) or
              len(scheduler['timers']) != 16 or
              len(set(scheduler['timers'])) != 16 or
              set(scheduler.get('attempted', [])) != set(scheduler['timers']) or
              PREOPEN not in scheduler['timers'] or GUARDIAN not in scheduler['timers'] or
              scheduler.get('preserved_iol_timer') != GUARDIAN or
              scheduler.get('operator_pause_timer') != PREOPEN,
              'SCHEDULER_HOLD_POLICY_CHANGED')
    return scheduler['timers']


def observed_proof():
    reject_if(BARRIER.is_symlink() or BARRIER.exists(), 'DEPLOY_BARRIER_PRESENT')
    timers = validate_origin()
    paper_zero_and_flat()
    running_containers()
    preopen = timer_state(PREOPEN)
    guardian = timer_state(GUARDIAN)
    reject_if(preopen != {'active':'inactive','enabled':'disabled'} or
              guardian != {'active':'active','enabled':'enabled'},
              'OPERATOR_HOLD_TIMERS_CHANGED')
    unit, rc = command('systemctl', 'show', PREOPEN, '-p', 'Unit', '--value')
    service, _ = command('systemctl', 'is-active', unit)
    reject_if(rc != 0 or unit != 'porota-preopen-rc6.service' or
              service not in {'inactive','failed'}, 'PREOPEN_SERVICE_CHANGED')
    # A175 owns 16 timers: the guardian is the only active carveout and the
    # operator preopen timer stays disabled (unlike a normal deploy rearm).
    for name in timers:
        if name not in {PREOPEN, GUARDIAN}:
            reject_if(timer_state(name)['active'] != 'inactive',
                      'SUSPENDED_TIMER_REACTIVATED')
    return {'ppi_snapshot':ppi_snapshot(), 'preopen_timer_state':preopen,
            'guardian_timer_state':guardian, 'owned_timer_count':len(timers)}


def valid_day(now):
    return now.astimezone(ZoneInfo('America/Argentina/Buenos_Aires')).date().isoformat() == DAY


def seal(run_id, attempt, now=None):
    reject_if(os.geteuid() != 0, 'ROOT_REQUIRED')
    reject_if(not re.fullmatch(r'[1-9][0-9]{0,15}', str(run_id)) or
              not re.fullmatch(r'[1-9][0-9]{0,3}', str(attempt)),
              'ACTION_ID_INVALID')
    inspect_host()
    now = now or datetime.now(timezone.utc)
    reject_if(not valid_day(now), 'A176_DAY_EXPIRED')
    reject_if(DIR.is_symlink() or not DIR.is_dir() or
              not root_owned(DIR.stat()) or DIR.stat().st_mode & 0o022,
              'RECEIPT_DIRECTORY_UNSAFE')
    lock_fd = os.open(LOCK, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(lock_fd)
        reject_if(not stat.S_ISREG(st.st_mode) or not root_owned(st) or
                  stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1,
                  'HOST_MUTEX_UNSAFE')
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Rejected('HOST_MUTEX_BUSY') from exc
        reject_if(BARRIER.is_symlink() or BARRIER.exists(), 'DEPLOY_BARRIER_PRESENT')
        proof = observed_proof()
        row = {'schema':'rc6.operator-no-go-continuation.v1',
               'status':'NO_GO_PENDING_EXPLICIT_REARM', 'business_day_art':DAY,
               'issued_at_utc':now.isoformat(),
               'operation_id':f'A176-{run_id}-{attempt}',
               'a151_origin_sha256':SHA['a151'],
               'a151_origin_operation_id':'A151-40686116017-1',
               'a175_candidate_sha':A175,
               'a175_quiescence_sha256':SHA['q'],
               'a175_scheduler_sha256':SHA['scheduler'],
               'a175_marker_sha256':SHA['marker'], 'a175_image_id':IMAGE,
               'paper_execution_authorized':False,
               'real_order_routes':'NOT_CALLED', 'real_orders_sent':0,
               'ppi_watch_accessed':False, 'systemd_mutations':0,
               'docker_mutations':0, 'paper_ledger_mutations':0, **proof}
        data = (json.dumps(row, sort_keys=True, separators=(',', ':'),
                           allow_nan=False) + '\n').encode('utf-8')
        digest = hashlib.sha256(data).hexdigest()
        if RECEIPT.exists() or RECEIPT.is_symlink():
            # Rerun after a successful link is strictly read-only and cannot
            # silently replace an earlier day's or runner's operator action.
            existing, existing_digest = read_guarded(RECEIPT)
            stable = {k:v for k,v in row.items() if k not in ('issued_at_utc','operation_id')}
            reject_if({k:v for k,v in existing.items() if k not in
                       ('issued_at_utc','operation_id')} != stable or
                      not valid_day(datetime.fromisoformat(existing['issued_at_utc'])) or
                      not re.fullmatch(r'A176-[1-9][0-9]{0,15}-[1-9][0-9]{0,3}',
                                       str(existing.get('operation_id'))),
                      'EXISTING_SEAL_DRIFT')
            digest = existing_digest
            written = False
        else:
            fd, temporary = tempfile.mkstemp(prefix='.a176-no-go-', dir=DIR)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                # Hard-link is atomic with no-overwrite semantics. Never
                # replace an existing receipt, even after an Actions retry.
                os.link(temporary, RECEIPT, follow_symlinks=False)
                directory_fd = os.open(DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                os.unlink(temporary)
            read_exact(RECEIPT, digest)
            written = True
        return {'schema':'RC6_A176_ADMIN_NO_GO_SEAL_V1',
                'status':'SEALED_NO_GO', 'operation_id':
                    (row['operation_id'] if written else existing['operation_id']),
                'receipt_sha256':digest, 'written_once':written,
                'business_day_art':DAY, 'paper_execution_authorized':False,
                'real_orders_sent':0, 'ppi_watch':'NOT_MUTATED',
                'host_mutations':1 if written else 0}
    finally:
        os.close(lock_fd)


def main():
    try:
        result = seal(os.environ.get('RC6_GH_RUN_ID', ''),
                      os.environ.get('RC6_GH_RUN_ATTEMPT', ''))
    except (Rejected, OSError, TypeError, KeyError, ValueError,
            sqlite3.Error, subprocess.TimeoutExpired, UnicodeError) as exc:
        result = {'schema':'RC6_A176_ADMIN_NO_GO_SEAL_V1', 'status':'RED',
                  'reason':str(exc) if isinstance(exc, Rejected) else type(exc).__name__,
                  'paper_execution_authorized':False}
    print('RC6_A176_ADMIN_NO_GO=' + json.dumps(result, sort_keys=True))
    return 0 if result['status'] == 'SEALED_NO_GO' else 2


if __name__ == '__main__':
    raise SystemExit(main())
