#!/usr/bin/env python3
"""One bounded, source-staged RC6 host preflight under the existing host flock.

The runner prepends STAGED_Q_GZIP_B64, STAGED_S_GZIP_B64 and their SHA256s to
this source on stdin. No host file, timer, container or SQLite row is changed.
The only output is an aggregate receipt with no account/provider payload.
"""
import base64
from datetime import datetime, timezone
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import types
import urllib.request
from zoneinfo import ZoneInfo

EXPECTED_ID = '594077619'
EXPECTED_REGION = 'nyc1'
ROOT = Path('/opt/porota-trading')
LOCK = Path('/run/lock/porota-rc6-deploy-v2.lock')
BARRIER = Path('/run/lock/porota-rc6-deploy-v2-attempt.json')
ART = ZoneInfo('America/Argentina/Buenos_Aires')
SHA = re.compile(r'[0-9a-f]{40}\Z')
HASH = re.compile(r'[0-9a-f]{64}\Z')
CODE = re.compile(r'[A-Z][A-Z0-9_]{2,99}\Z')
EXPECTED_OLD_SHA = '2325eba329878c62a61cb47bb66d33ccf279bd4a'


def metadata(name):
    if name not in {'id', 'region'}:
        raise ValueError('METADATA_NAME_INVALID')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://169.254.169.254/metadata/v1/' + name,
                     timeout=1.5) as response:
        if response.status != 200:
            raise ValueError('METADATA_STATUS_RED')
        value = response.read(64).decode('ascii').strip()
    if not re.fullmatch(r'[0-9]{1,20}' if name == 'id' else r'[a-z0-9-]{2,20}', value):
        raise ValueError('METADATA_VALUE_RED')
    return value


def code(exc):
    value = str(exc).split(':', 1)[0]
    return value if CODE.fullmatch(value) else type(exc).__name__.upper()


def stage(encoded, expected_hash, name, bound):
    if (not isinstance(encoded, str) or len(encoded) > 180000 or
            not isinstance(expected_hash, str) or not HASH.fullmatch(expected_hash)):
        raise ValueError('STAGED_SOURCE_BOUND_RED')
    compressed = base64.b64decode(encoded, validate=True)
    if not 0 < len(compressed) < 110000:
        raise ValueError('STAGED_COMPRESSED_BOUND_RED')
    source = gzip.decompress(compressed)
    if not 20000 < len(source) < bound or hashlib.sha256(source).hexdigest() != expected_hash:
        raise ValueError('STAGED_SOURCE_HASH_OR_SIZE_RED')
    module = types.ModuleType(name)
    exec(compile(source, 'candidate/' + name + '.py', 'exec'), module.__dict__)
    return module


def read_lock():
    if os.geteuid() != 0:
        raise ValueError('ROOT_REQUIRED')
    directory = LOCK.parent.lstat()
    if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != 0 or
            directory.st_mode & stat.S_IWOTH and not directory.st_mode & stat.S_ISVTX):
        raise ValueError('MUTEX_PARENT_UNSAFE')
    fd = os.open(LOCK, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        current = LOCK.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or
                stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or
                (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)):
            raise ValueError('MUTEX_IDENTITY_RED')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            BARRIER.lstat()  # Detect a dangling symlink too; never reconcile.
        except FileNotFoundError:
            pass
        else:
            raise ValueError('BARRIER_PRIOR_ATTEMPT_REVIEW_REQUIRED')
        return fd
    except BaseException:
        os.close(fd)
        raise


def bounded_command(*argv):
    try:
        result = subprocess.run(argv, capture_output=True, text=True,
                                timeout=1.5, check=False)
        if len(result.stdout) > 65536:
            raise ValueError('SYSTEMCTL_OUTPUT_BOUND_RED')
        return result.stdout.strip(), result.returncode
    except (OSError, subprocess.SubprocessError):
        return '', 124


def bounded_quiescence_command(argv):
    # The candidate's exact issues-only health command is read-only; -B also
    # prevents an unexpected import from writing pycache in its container.
    if argv[:4] == ['docker', 'exec', 'porota_critical_approval_rc6', 'python']:
        argv = ['docker', 'exec', '-e', 'PYTHONDONTWRITEBYTECODE=1',
                'porota_critical_approval_rc6', 'python', '-B', *argv[4:]]
    try:
        result = subprocess.run(argv, capture_output=True, text=True,
                                timeout=7, check=False)
        if result.returncode:
            raise ValueError('QUIESCENCE_COMMAND_RED')
        if len(result.stdout) > 32768:
            raise ValueError('QUIESCENCE_OUTPUT_BOUND_RED')
        return result.stdout
    except subprocess.TimeoutExpired as exc:
        raise ValueError('QUIESCENCE_COMMAND_TIMEOUT') from exc


def scheduler_receipt(s, raw_policy, proof):
    names = s.timer_policy(raw_policy)
    if len(names) != 16 or s.preserved_iol_guard(raw_policy) != s.IOL_OAUTH_GUARD_TIMER:
        raise ValueError('TIMER_POLICY_RED')
    records = {}
    states = []
    for name in names:
        active, ar = s.command('systemctl', 'is-active', name)
        enabled, er = s.command('systemctl', 'is-enabled', name)
        service, sr = s.command('systemctl', 'show', name, '-p', 'Unit', '--value')
        service_state, cr = s.command('systemctl', 'is-active', service)
        expected = ('inactive', 'disabled') if name == s.OPERATOR_PREOPEN_TIMER else (
            'inactive', 'enabled')
        good = (active, enabled) == expected and service == name.removesuffix('.timer') + '.service'
        good = good and service_state not in {'active', 'activating', 'reloading', 'deactivating', ''}
        good = good and sr == 0 and ar in {0, 3} and er in {0, 1} and cr in {0, 3}
        records[name] = {'active': active if active in {'active','inactive'} else 'UNKNOWN',
                         'enabled': enabled if enabled in {'enabled','disabled'} else 'UNKNOWN',
                         'service_state': service_state if service_state in
                         {'active','inactive','failed'} else 'UNKNOWN',
                         'migration_ready': good}
        states.append(good)
    before = s.ppi_snapshot()
    a125 = s.operator_preopen_hold_evidence()
    prior_path = s.ROOT/('scheduler-suspend-'+s.A143_PENDING_CANDIDATE+'.json')
    prior, prior_sha = s._operator_pause_json(prior_path,65536)
    a138_path = s.ROOT/('scheduler-suspend-'+s.FAILED_A138_SUSPENSION_CANDIDATE+'.json')
    a138, a138_sha = s._operator_pause_json(a138_path,65536)
    chain = (prior.get('candidate_sha') == s.A143_PENDING_CANDIDATE and
             prior.get('status') == 'SUSPENDED' and
             prior.get('policy_sha256') == s.A138_ORIGINAL_TIMER_POLICY_SHA256 and
             prior.get('timers') == names and prior.get('ppi_before') == before and
             set(prior.get('attempted',[])) == set(names) and
             prior.get('paused_from') == s.FAILED_A138_SUSPENSION_CANDIDATE and
             prior.get('prior_receipt_sha256') == a138_sha and
             prior.get('adopted_a138_operator_hold') is True and
             prior.get('operator_pause_timer') == s.OPERATOR_PREOPEN_TIMER and
             prior.get('operator_pause_receipt_sha256') == a125 and
             prior.get('rescue_failure_disposition') == 'RESCUE_CANDIDATE_UNVALIDATED_HELD' and
             a138.get('candidate_sha') == s.FAILED_A138_SUSPENSION_CANDIDATE and
             a138.get('status') == 'SUSPENDED' and
             a138.get('policy_sha256') == s.A138_ORIGINAL_TIMER_POLICY_SHA256 and
             a138.get('timers') == names and a138.get('ppi_before') == before and
             a138.get('operator_pause_receipt_sha256') == a125 and
             proof.get('flat_book',{}).get('real_orders_sent') == 0)
    return {'owned_count':len(names),'states':records,
            'all_held_for_migration':all(states),'a138_a143_chain_exact':bool(chain),
            'ppi_snapshot_matches_prior':prior.get('ppi_before') == before,
            'ppi_snapshot_sha256':hashlib.sha256(json.dumps(before,sort_keys=True).encode()).hexdigest(),
            'prior_receipt_sha256':prior_sha}


def guardian_state(s, now):
    name = s.IOL_OAUTH_GUARD_TIMER
    service = s.IOL_OAUTH_GUARD_SERVICE
    actual_service, code_service = s.command(
        'systemctl','show',name,'-p','Unit','--value')
    if code_service or actual_service != service:
        raise ValueError('IOL_SERVICE_IDENTITY_RED')
    path = ROOT/'data/market/iol_oauth_guardian_state.json'
    state, age_seconds = 'NO_VERIFICADO', None
    if (path.is_file() and not path.is_symlink() and
            path.stat().st_nlink == 1 and path.stat().st_size <= 8192):
        payload = json.loads(path.read_bytes())
        value = payload.get('state') if type(payload) is dict else None
        if (type(payload) is dict and
                payload.get('schema') == 'POROTA_RC6_IOL_OAUTH_GUARDIAN_STATE_V1'
                and isinstance(value,str) and CODE.fullmatch(value)):
            state = value
        try:
            observed = datetime.fromisoformat(str(payload.get('checked_at')).replace('Z','+00:00'))
            if observed.tzinfo:
                age_seconds = round((now-observed.astimezone(timezone.utc)).total_seconds(),1)
        except (TypeError,ValueError,AttributeError):
            pass
    return {'unit_identity_exact':True,'state':state,'checked_age_s':age_seconds,
            'fresh_under_1900s':age_seconds is not None and 0 <= age_seconds <= 1900,
            'pending_guardian_restart_under_canonical_migration':True}


def main(candidate_sha, tree_sha, policy_b64, disk_policy_b64, required_bytes):
    now = datetime.now(timezone.utc)
    receipt = {'schema':'rc6.a160.held-preflight-ro.v1',
               'at_utc':now.isoformat(),'at_art':now.astimezone(ART).isoformat(),
               'candidate_sha':candidate_sha,'candidate_tree':tree_sha,
               'host_mutations':0,'iol_provider_requests':0,'ppi_watch':'STATE_ONLY',
               'real_orders_sent':0,'operational_go':False,
               'runtime_validation':'NOT_ATTESTED_BY_PREFLIGHT'}
    fd = None
    try:
        if (not SHA.fullmatch(candidate_sha) or not SHA.fullmatch(tree_sha) or
                not str(required_bytes).isdigit() or int(required_bytes) <= 0):
            raise ValueError('CANDIDATE_OR_DISK_INPUT_RED')
        droplet = {'id':metadata('id'),'region':metadata('region')}
        receipt['droplet'] = droplet
        if droplet != {'id':EXPECTED_ID,'region':EXPECTED_REGION}:
            raise ValueError('DROPLET_IDENTITY_MISMATCH')
        fd = read_lock()
        receipt['mutex'] = 'HELD_EXISTING_ROOT_FLOCK_NO_BARRIER_WRITE'
        q = stage(STAGED_Q_GZIP_B64,STAGED_Q_SHA256,
                  'rc6_a160_staged_quiescence',190000)
        s = stage(STAGED_S_GZIP_B64,STAGED_S_SHA256,
                  'rc6_a160_staged_scheduler',110000)
        q.command = bounded_quiescence_command
        s.command = bounded_command
        proof = q.a143_pending_running_proof(
            allow_a151_held_open=True,
            operator_a151_proof=s.exact_a151_operator_preopen_pause,
            operator_timer_disabled=s.operator_preopen_timer_disabled)
        if proof.get('source_sha') != EXPECTED_OLD_SHA:
            raise ValueError('OLD_RUNTIME_SOURCE_MISMATCH')
        held = proof.get('operator_hold')
        receipt['admission'] = {'state':'GREEN',
            'mode':'A151_HELD_OPEN' if held else 'CLOSED_A143_PENDING_FIX_FORWARD',
            'old_release_sha':proof['source_sha'],
            'marker_sha256':proof['marker_sha256'],
            'frozen_sha256':proof['frozen_sha256'],
            'prior_receipt_sha256':proof['prior_receipt_sha256'],
            'docker_ids_and_image_match_prior':True,
            'flat_three_books':True,'heartbeat_fresh_under_180s':True,
            'critical_issues_only_healthy':proof['critical_admission'].get('capability') == 'issues_only',
            'paper_state': [proof['flat_book']['process_state'],proof['flat_book']['session_state']],
            'a151_receipt_sha256':held.get('operator_receipt_sha256') if held else None,
            'paper_execution_authorized':False}
        if (not isinstance(policy_b64,str) or len(policy_b64) > 65536 or
                not isinstance(disk_policy_b64,str) or len(disk_policy_b64) > 16384):
            raise ValueError('POLICY_BOUND_RED')
        raw_policy = base64.b64decode(policy_b64,validate=True)
        raw_disk_policy = base64.b64decode(disk_policy_b64,validate=True)
        if len(raw_policy) > 32768 or len(raw_disk_policy) > 8192:
            raise ValueError('POLICY_BOUND_RED')
        receipt['timers'] = scheduler_receipt(s,raw_policy,proof)
        receipt['iol_guardian'] = guardian_state(s,now)
        policy = json.loads(raw_disk_policy)
        min_inode = float(policy['disk']['min_inode_free_percent'])
        if not 0 < min_inode <= 100:
            raise ValueError('DISK_POLICY_INODES_RED')
        usage = shutil.disk_usage('/')
        vfs = os.statvfs('/')
        percent = round(100*vfs.f_ffree/vfs.f_files,6) if vfs.f_files else 0
        receipt['disk'] = {'free_bytes':usage.free,
                           'required_pretransfer_bytes':int(required_bytes),
                           'margin_bytes':usage.free-int(required_bytes),
                           'inode_free_percent':percent,
                           'min_inode_free_percent':min_inode,
                           'bytes_gate':usage.free >= int(required_bytes),
                           'inodes_gate':percent >= min_inode}
        receipt['status'] = ('GREEN' if receipt['admission']['critical_issues_only_healthy']
             and receipt['timers']['all_held_for_migration']
             and receipt['timers']['a138_a143_chain_exact']
             and receipt['disk']['bytes_gate']
             and receipt['disk']['inodes_gate'] else 'RED')
    except Exception as exc:
        receipt['status'] = 'RED'
        receipt['error_code'] = code(exc)
    finally:
        if fd is not None:
            os.close(fd)
    print('RC6_A160_HELD_PREFLIGHT_RO='+json.dumps(receipt,sort_keys=True,
          separators=(',',':'),allow_nan=False))
    return 0 if receipt['status']=='GREEN' else 2


if __name__ == '__main__':
    signal.alarm(75)
    if len(sys.argv) != 6:
        raise SystemExit('EXPECTED_SHA_TREE_POLICY_DISK_POLICY_REQUIRED_BYTES')
    raise SystemExit(main(*sys.argv[1:]))
