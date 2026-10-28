#!/usr/bin/env python3
"""One bounded, source-staged A175 preflight under the existing host flock.

The runner prepends STAGED_Q_GZIP_B64, STAGED_S_GZIP_B64 and their SHA256s to
this source on stdin. No host file, timer, container or SQLite row is changed.
The only output is an aggregate receipt with no account/provider payload.
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
import signal
import stat
import subprocess
import sys
import types
import urllib.request
import zlib
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
# Product identity is fixed; the workflow remains disabled until its one
# successful Predeploy artifact and digest are verified.
EXPECTED_CANDIDATE = '1a36eae75a2dcbd0420c324b5ef0bfea07a02da8'
EXPECTED_TREE = 'f45177bbb41fc179ecfe862799848a826e0e5c93'
EXPECTED_PRIOR = 'b6e6ce75f29fe117aaa42b0bfcd9386ea93a0aa0'
EXPECTED_PRIOR_TREE = 'ac82bcb2617748294c9950a009864dbf95e336f4'
EXPECTED_PRIOR_DEPLOY = 'ff2775933b1565bb3a43fa130f9aa6359ecbdf48'
EXPECTED_PRIOR_RECEIPT = '364c9bb9f013da603345c90219e7c889d5b04ecff951afe08f4bfc7ec03c1528'
EXPECTED_PRIOR_SCHEDULER = 'e0ddc6a0b50a83510b172a7653e5e26cad309aaa4a49207c9be7aa4cded6cec6'
EXPECTED_PRIOR_MARKER = '0191482515683e5184871d63e6030208e0b22ddfc727845b06c4fc9b59d5c6f9'
EXPECTED_PRIOR_IMAGE = 'sha256:9ca6e7ca98f11bbb82353434f54ffab15b218ec54319c83ae1661aaf897a40e9'
EXPECTED_IDS = {
    'porota_production_observer':'22235a9b2d808ac1f8f653939538fd382469d1ec4c8d150262b0a3d6d97d9dbd',
    'porota_production_dashboard':'dbbb264e2afc0306f4c5e3cbae5fa49bdfbde9204ab660902ccf0adecc1458d7',
}


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
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    source = inflater.decompress(compressed, bound)
    if (not inflater.eof or inflater.unconsumed_tail or inflater.unused_data or
            not 20000 < len(source) < bound or
            hashlib.sha256(source).hexdigest() != expected_hash):
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
    if (not argv or argv[0] != 'systemctl' or len(argv) < 3 or
            argv[1] not in {'is-active','is-enabled','show','list-unit-files'} or
            (argv[1] == 'show' and (len(argv) != 6 or argv[3:] != ('-p','Unit','--value'))) or
            (argv[1] == 'list-unit-files' and argv[2:] != ('--no-legend',)) or
            (argv[1] in {'is-active','is-enabled'} and len(argv) != 3)):
        raise ValueError('SYSTEMCTL_MUTATION_FORBIDDEN')
    try:
        result = subprocess.run(argv, capture_output=True, text=True,
                                timeout=1.5, check=False)
        if len(result.stdout) > 65536:
            raise ValueError('SYSTEMCTL_OUTPUT_BOUND_RED')
        return result.stdout.strip(), result.returncode
    except (OSError, subprocess.SubprocessError):
        return '', 124


def bounded_quiescence_command(argv):
    if (argv[:2] == ['docker','inspect'] and len(argv) == 3 and
            argv[2] in {*EXPECTED_IDS, 'porota_critical_approval_rc6'}):
        pass
    elif (argv[:3] == ['docker','image','inspect'] and len(argv) == 4 and
            argv[3] in {'porota-trading-bot:17.0.0-rc6',
                'porota-trading-bot:17.0.0-rc6-candidate-'+EXPECTED_PRIOR}):
        pass
    elif (argv[:5] == ['docker','exec','porota_critical_approval_rc6','python','-c'] and
            len(argv) == 6 and argv[5] ==
            "from fm_critical_approval_unix_runtime_rc6 import UnixGithubIssuesClient; "
            "h=UnixGithubIssuesClient().health(); "
            "assert h.get('status')=='ok' and h.get('capability')=='issues_only'; "
            "print('RC6_CRITICAL_ISSUES_ONLY=GREEN')"):
        # -B and PYTHONDONTWRITEBYTECODE keep the health import read-only.
        argv = ['docker', 'exec', '-e', 'PYTHONDONTWRITEBYTECODE=1',
                'porota_critical_approval_rc6', 'python', '-B', *argv[4:]]
    else:
        raise ValueError('QUIESCENCE_MUTATION_FORBIDDEN')
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


def scheduler_receipt(s, raw_policy, proof, q):
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
        expected = (('active', 'enabled') if name == s.IOL_OAUTH_GUARD_TIMER else
                    ('inactive', 'disabled') if name == s.OPERATOR_PREOPEN_TIMER else
                    ('inactive', 'enabled'))
        good = (active, enabled) == expected and service == name.removesuffix('.timer') + '.service'
        good = good and service_state not in {'active', 'activating', 'reloading', 'deactivating', ''}
        if name == s.IOL_OAUTH_GUARD_TIMER:
            good = good and service_state == 'inactive'
        good = good and sr == 0 and ar in {0, 3} and er in {0, 1} and cr in {0, 3}
        records[name] = {'active': active if active in {'active','inactive'} else 'UNKNOWN',
                         'enabled': enabled if enabled in {'enabled','disabled'} else 'UNKNOWN',
                         'service_state': service_state if service_state in
                         {'active','inactive','failed'} else 'UNKNOWN',
                         'held': good}
        states.append(good)
    before = s.ppi_snapshot()
    prior_path = s.ROOT/('scheduler-suspend-'+EXPECTED_PRIOR+'.json')
    prior, prior_sha = s._operator_pause_json(prior_path,65536)
    chain = (prior.get('candidate_sha') == EXPECTED_PRIOR and
             prior_sha == EXPECTED_PRIOR_SCHEDULER and
             prior.get('status') == 'SUSPENDED' and
             prior.get('policy_sha256') == hashlib.sha256(raw_policy).hexdigest() and
             prior.get('timers') == names and prior.get('ppi_before') == before and
             set(prior.get('attempted',[])) == set(names) and
             prior.get('paused_from') == q.A171_FAILED_CANDIDATE and
             prior.get('a171_origin_sha256') == q.A171_FAILED_RECEIPT_SHA256 and
             prior.get('a171_running_origin') is True and
             prior.get('prior_receipt_sha256') == '16af3abfde22a62bc1e4c9b946ef3e2f09aeb561b18f3c5a692359bc5a85ec52' and
             prior.get('preserved_iol_timer') == s.IOL_OAUTH_GUARD_TIMER and
             prior.get('operator_pause_timer') == s.OPERATOR_PREOPEN_TIMER and
             prior.get('operator_pause_receipt_sha256') == s.operator_preopen_hold_evidence() and
             prior.get('rescue_failure_disposition') == 'A172_NEW_CANDIDATE_UNVALIDATED_HELD' and
             proof.get('flat_book',{}).get('real_orders_sent') == 0)
    return {'owned_count':len(names),'states':records,
            'all_held_for_migration':all(states),'a174_chain_exact':bool(chain),
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
    if (path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1 or
            not 0 < path.stat().st_size <= 8192):
        raise ValueError('IOL_GUARDIAN_STATE_PATH_RED')
    payload = json.loads(path.read_bytes())
    if (type(payload) is not dict or
            payload.get('schema') != 'POROTA_RC6_IOL_OAUTH_GUARDIAN_STATE_V1' or
            payload.get('state') != 'AUTHENTICATED' or
            payload.get('reason') != 'OFFHOURS_OAUTH_GUARDIAN' or
            type(payload.get('incident_seq')) is not int or
            payload['incident_seq'] < 0):
        raise ValueError('IOL_GUARDIAN_AUTH_STATE_RED')
    try:
        checked = datetime.fromisoformat(payload['checked_at'].replace('Z','+00:00'))
        authenticated = datetime.fromisoformat(
            payload['last_authenticated_at'].replace('Z','+00:00'))
        age = (now - checked.astimezone(timezone.utc)).total_seconds()
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError('IOL_GUARDIAN_TIMESTAMP_RED') from exc
    if (checked.tzinfo is None or authenticated.tzinfo is None or
            checked != authenticated or not 0 <= age <= 1900):
        raise ValueError('IOL_GUARDIAN_FRESHNESS_RED')
    return {'unit_identity_exact':True,'state':'AUTHENTICATED',
            'reason':'OFFHOURS_OAUTH_GUARDIAN',
            'checked_age_s':round(age,1),'fresh_under_1900s':True,
            'incident_seq':payload['incident_seq']}


class PreviewWriteBoundary(Exception):
    """The complete scheduler proof reached its first persist, which we deny."""


def scheduler_adoption_preview(s, q, candidate, raw_policy):
    """Replay A174 receipt adoption on the real host without a write syscall.

    This calls the candidate's exact adoption proof directly. Unlike run(),
    it never calls ROOT.mkdir. Every systemctl mutation and the first receipt
    persist are intercepted before they can reach the host.
    """
    if s.ROOT != ROOT/'data/deploy':
        raise ValueError('SCHEDULER_PREVIEW_ROOT_RED')
    path=s.ROOT/('scheduler-suspend-'+candidate+'.json')
    if path.is_symlink() or path.exists():
        raise ValueError('SCHEDULER_PREVIEW_RECEIPT_EXISTS')
    original_command=s.command
    def read_only_command(*argv):
        if (len(argv)<2 or argv[0]!='systemctl' or
                argv[1] not in {'is-active','is-enabled','show','list-unit-files'}):
            raise ValueError('SCHEDULER_PREVIEW_MUTATION_FORBIDDEN')
        return original_command(*argv)
    def no_persist(target,payload):
        if target != path or persisted:
            raise ValueError('SCHEDULER_PREVIEW_PERSIST_TARGET_RED')
        persisted.append(payload)
        raise PreviewWriteBoundary()
    persisted=[]
    s.command=read_only_command
    s.persist=no_persist
    s.a143_staged_module=lambda:q
    names=s.timer_policy(raw_policy)
    if len(names)!=16 or s.preserved_iol_guard(raw_policy)!=s.IOL_OAUTH_GUARD_TIMER:
        raise ValueError('SCHEDULER_PREVIEW_POLICY_RED')
    before=s.ppi_snapshot()
    states=[(s.command('systemctl','is-active',name)[0],
             s.command('systemctl','is-enabled',name)[0]) for name in names]
    try:
        s.adopt_a174_running_hold(candidate,path,names,
                                 hashlib.sha256(raw_policy).hexdigest(),before,states)
    except PreviewWriteBoundary:
        pass
    else:
        raise ValueError('SCHEDULER_PREVIEW_NO_WRITE_BOUNDARY')
    row=persisted[0]
    if (row.get('candidate_sha')!=candidate or row.get('status')!='SUSPENDED' or
            row.get('paused_from')!=EXPECTED_PRIOR or
            row.get('a174_origin_sha256')!=EXPECTED_PRIOR_RECEIPT or
            row.get('a174_running_origin') is not True or
            row.get('prior_receipt_sha256')!=EXPECTED_PRIOR_SCHEDULER or
            row.get('ppi_before')!=before or
            set(row.get('attempted',[]))!=set(names)):
        raise ValueError('SCHEDULER_PREVIEW_RECEIPT_RED')
    return {'status':'GREEN','write_intercepted':True,'systemd_mutations':0,
            'receipt_persisted':False,'a174_origin_exact':True,'owned_timers':16}


def main(candidate_sha, tree_sha, policy_b64, disk_policy_b64, required_bytes):
    now = datetime.now(timezone.utc)
    receipt = {'schema':'rc6.a175.running-a174-preflight-ro.v1',
               'at_utc':now.isoformat(),'at_art':now.astimezone(ART).isoformat(),
               'candidate_sha':candidate_sha,'candidate_tree':tree_sha,
               'host_mutations':0,'iol_provider_requests':0,'ppi_watch':'STATE_ONLY',
               'real_orders_sent':'UNVERIFIED','operational_go':False,
               'runtime_validation':'NOT_ATTESTED_BY_PREFLIGHT'}
    fd = None
    try:
        if (not SHA.fullmatch(EXPECTED_CANDIDATE) or
                not SHA.fullmatch(EXPECTED_TREE) or
                candidate_sha != EXPECTED_CANDIDATE or tree_sha != EXPECTED_TREE or
                not str(required_bytes).isdigit() or int(required_bytes) <= 0):
            raise ValueError('CANDIDATE_OR_DISK_INPUT_RED')
        droplet = {'id':metadata('id'),'region':metadata('region')}
        receipt['droplet'] = droplet
        if droplet != {'id':EXPECTED_ID,'region':EXPECTED_REGION}:
            raise ValueError('DROPLET_IDENTITY_MISMATCH')
        fd = read_lock()
        receipt['mutex'] = 'HELD_EXISTING_ROOT_FLOCK_NO_BARRIER_WRITE'
        q = stage(STAGED_Q_GZIP_B64,STAGED_Q_SHA256,
                  'rc6_a175_staged_quiescence',230000)
        s = stage(STAGED_S_GZIP_B64,STAGED_S_SHA256,
                  'rc6_a175_staged_scheduler',130000)
        q.command = bounded_quiescence_command
        s.command = bounded_command
        if (q.A174_FAILED_CANDIDATE != EXPECTED_PRIOR or
                q.A174_CANDIDATE_TREE_SHA != EXPECTED_PRIOR_TREE or
                q.A174_DEPLOY_SHA != EXPECTED_PRIOR_DEPLOY or
                q.A174_FAILED_RECEIPT_SHA256 != EXPECTED_PRIOR_RECEIPT or
                q.A174_SCHEDULER_RECEIPT_SHA256 != EXPECTED_PRIOR_SCHEDULER or
                q.A174_MARKER_SHA256 != EXPECTED_PRIOR_MARKER or
                q.A174_LOADED_IMAGE_ID != EXPECTED_PRIOR_IMAGE or
                q.A174_RUNNING_IDS != EXPECTED_IDS):
            raise ValueError('STAGED_A174_PRIOR_CONSTANTS_RED')
        proof = q.a174_failed_running_proof(
            candidate_sha,
            operator_a151_proof=s.exact_a151_operator_preopen_pause,
            operator_timer_disabled=s.operator_preopen_timer_disabled,
            require_installed_a174_source=True)
        if (proof.get('origin_candidate') != EXPECTED_PRIOR or
                proof.get('origin_sha256') != EXPECTED_PRIOR_RECEIPT or
                proof.get('image_id') != EXPECTED_PRIOR_IMAGE or
                proof.get('marker_sha256') != EXPECTED_PRIOR_MARKER or
                {k:v.get('id') for k,v in proof.get('containers',{}).items()} != EXPECTED_IDS or
                proof.get('flat_book',{}).get('real_orders_sent') != 0 or
                proof.get('flat_book',{}).get('process_state') != 'WAITING_MARKET' or
                proof.get('flat_book',{}).get('session_state') not in
                    {'CLOSED','MARKET_CLOSED'}):
            raise ValueError('A174_RUNNING_PROOF_RED')
        receipt['real_orders_sent'] = 0
        receipt['admission'] = {'state':'GREEN',
            'mode':'CLOSED_A174_PENDING_FIX_FORWARD',
            'prior_release_sha':proof['origin_candidate'],
            'prior_receipt_sha256':proof['origin_sha256'],
            'prior_marker_sha256':proof['marker_sha256'],
            'prior_runtime_image_id':proof['image_id'],
            'docker_ids_and_image_match_prior':True,
            'docker_ids':EXPECTED_IDS,
            'flat_three_books':True,'running_paper_book_proven':True,
            'critical_issues_only_healthy':True,
            'paper_state': [proof['flat_book']['process_state'],proof['flat_book']['session_state']],
            'a151_receipt_sha256':proof['operator_receipt_sha256'],
            'paper_execution_authorized':False}
        if (not isinstance(policy_b64,str) or len(policy_b64) > 65536 or
                not isinstance(disk_policy_b64,str) or len(disk_policy_b64) > 16384):
            raise ValueError('POLICY_BOUND_RED')
        raw_policy = base64.b64decode(policy_b64,validate=True)
        raw_disk_policy = base64.b64decode(disk_policy_b64,validate=True)
        if len(raw_policy) > 32768 or len(raw_disk_policy) > 8192:
            raise ValueError('POLICY_BOUND_RED')
        receipt['timers'] = scheduler_receipt(s,raw_policy,proof,q)
        receipt['iol_guardian'] = guardian_state(s,now)
        if (not receipt['timers']['all_held_for_migration'] or
                not receipt['timers']['a174_chain_exact'] or
                not receipt['iol_guardian']['fresh_under_1900s']):
            raise ValueError('SCHEDULER_PREVIEW_PREREQUISITE_RED')
        receipt['scheduler_preview'] = scheduler_adoption_preview(
            s,q,candidate_sha,raw_policy)
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
             and receipt['timers']['a174_chain_exact']
             and receipt['timers']['ppi_snapshot_matches_prior']
             and receipt['iol_guardian']['fresh_under_1900s']
             and receipt['scheduler_preview']['status']=='GREEN'
             and receipt['disk']['bytes_gate']
             and receipt['disk']['inodes_gate'] else 'RED')
    except Exception as exc:
        receipt['status'] = 'RED'
        receipt['error_code'] = code(exc)
    finally:
        if fd is not None:
            os.close(fd)
    print('RC6_A175_RUNNING_PREFLIGHT_RO='+json.dumps(receipt,sort_keys=True,
          separators=(',',':'),allow_nan=False))
    return 0 if receipt['status']=='GREEN' else 2


if __name__ == '__main__':
    signal.alarm(75)
    if len(sys.argv) != 6:
        raise SystemExit('EXPECTED_SHA_TREE_POLICY_DISK_POLICY_REQUIRED_BYTES')
    raise SystemExit(main(*sys.argv[1:]))
