#!/usr/bin/env python3
"""Read-only A178 preflight of exactly A175-running/PAPER-0 after reclaim.

Inputs are carried by an independently verified, immutable Predeploy V2
artifact. The runner prepends STAGED_Q/S_GZIP_B64 and SHA256 to this source;
no host file is installed or edited. Only safe reads and a nonblocking flock.
"""
from __future__ import annotations

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
import subprocess
import sys
import types
import urllib.request
import zlib
from zoneinfo import ZoneInfo

ROOT = Path('/opt/porota-trading')
LOCK = Path('/run/lock/porota-rc6-deploy-v2.lock')
BARRIER = Path('/run/lock/porota-rc6-deploy-v2-attempt.json')
DAY = '2026-10-29'
EXPECTED_CANDIDATE = '7d62d2f4ed353231da8566a360a1179e99bcc7eb'
EXPECTED_TREE = '31ced11ce047bfa092f5398c13aeb37b74afd629'
EXPECTED_SEAL = '3218ccf5296306068d051b12588c4fac253a768ea3c81e0fc31c501adb34338c'
ACTIVE_IMAGE = 'sha256:9841cba8df3345fa22e39457645ba250431ad1f465ce4204d92f73f7816215b9'
CRITICAL_IMAGE = 'sha256:586464c20b74ab3a7ba842df8dfafcd76f21eb4c7bed553f23f6f187924ff735'
HASH = re.compile(r'[0-9a-f]{64}\Z')
GIT_SHA = re.compile(r'[0-9a-f]{40}\Z')


def stage(encoded, digest, name, *, maximum):
    if (not isinstance(encoded, str) or not 0 < len(encoded) < 180000 or
            not isinstance(digest, str) or not HASH.fullmatch(digest)):
        raise ValueError('A178_STAGED_INPUT_RED')
    compressed = base64.b64decode(encoded, validate=True)
    if not 0 < len(compressed) < 110000:
        raise ValueError('A178_STAGED_COMPRESSED_RED')
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    source = inflater.decompress(compressed, maximum)
    if (not inflater.eof or inflater.unconsumed_tail or inflater.unused_data or
            not 20000 < len(source) < maximum or
            hashlib.sha256(source).hexdigest() != digest):
        raise ValueError('A178_STAGED_SOURCE_RED')
    module = types.ModuleType(name)
    exec(compile(source, 'candidate/' + name + '.py', 'exec'), module.__dict__)
    return module


def metadata():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    values = []
    for name in ('id', 'region'):
        with opener.open('http://169.254.169.254/metadata/v1/' + name,
                         timeout=2) as response:
            if response.status != 200:
                raise ValueError('A178_METADATA_HTTP_RED')
            values.append(response.read(65).decode('ascii').strip())
    if values != ['594077619', 'nyc1']:
        raise ValueError('A178_DROPLET_CHANGED')
    return {'id':values[0],'region':values[1]}


def read_lock():
    if os.geteuid() != 0:
        raise ValueError('A178_ROOT_REQUIRED')
    fd = os.open(LOCK, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        current = os.lstat(LOCK)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or
                stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1 or
                (st.st_dev, st.st_ino) != (current.st_dev, current.st_ino)):
            raise ValueError('A178_MUTEX_IDENTITY_RED')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if BARRIER.is_symlink() or BARRIER.exists():
            raise ValueError('A178_ATTEMPT_BARRIER_PRESENT')
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_docker_images():
    output = subprocess.run(['docker','image','ls','-aq','--no-trunc'],
                            capture_output=True,text=True,timeout=10,check=True).stdout
    images=set(output.splitlines())
    if images != {ACTIVE_IMAGE,CRITICAL_IMAGE}:
        raise ValueError('A178_POSTCLEANUP_IMAGE_INVENTORY_RED')
    return images


def timer_hold(s):
    policy = (ROOT/'ops/policy/host-control-plane-reconciliation-v2.json').read_bytes()
    names = s.timer_policy(policy)
    if len(names) != 16:
        raise ValueError('A178_TIMER_POLICY_CHANGED')
    states={}
    for name in names:
        active,rc=s.command('systemctl','is-active',name)
        enabled,re=s.command('systemctl','is-enabled',name)
        states[name]={'active':active,'enabled':enabled}
        if name == s.OPERATOR_PREOPEN_TIMER:
            good=(active,enabled,rc,re)==('inactive','disabled',3,1)
        elif name == s.IOL_OAUTH_GUARD_TIMER:
            good=(active,enabled,rc,re)==('active','enabled',0,0)
        else:
            good=active=='inactive' and rc==3 and enabled in {'disabled','enabled'}
        if not good:
            raise ValueError('A178_TIMER_HOLD_CHANGED')
    return {'count':len(names),'preopen':states[s.OPERATOR_PREOPEN_TIMER],
            'guardian':states[s.IOL_OAUTH_GUARD_TIMER]}


def capacity(required, policy_b64):
    if not isinstance(required, int) or required < 2 * 1024**3:
        raise ValueError('A178_DISK_FORMULA_RED')
    raw=base64.b64decode(policy_b64, validate=True)
    policy=json.loads(raw)
    vfs=os.statvfs('/')
    inode_free=100 * vfs.f_ffree / vfs.f_files
    free=shutil.disk_usage('/').free
    minimum=float(policy['disk']['min_inode_free_percent'])
    if not 1 <= minimum <= 50 or free < required or inode_free < minimum:
        raise ValueError('A178_DISK_OR_INODES_RED')
    return {'free_bytes':free,'required_pretransfer_bytes':required,
            'margin_bytes':free-required,'inodes_free_percent':inode_free,
            'minimum_inodes_percent':minimum}


def diagnose_origin_book(q):
    """Safe, bounded read-only fields if A175 PAPER/NO_GO admission rejects."""
    try:
        book=q.rescue_flat_book()  # Also verifies all three PAPER ledgers flat.
    except (ValueError, OSError) as exc:
        return {'flat_book':'UNVERIFIED','reason':type(exc).__name__+':'+str(exc)}
    try:
        admission=q.admit(
            allow_market_open_suspend=
                (book['process_state'],book['session_state'])==('RUNNING','MARKET_OPEN'))
    except (ValueError, OSError) as exc:
        admission='RED:'+str(exc)
    try:
        capability=q.critical_admission().get('capability')
    except (ValueError, OSError) as exc:
        capability='RED:'+str(exc)
    return {'flat_book':'VERIFIED','process_state':book['process_state'],
            'session_state':book['session_state'],
            'source_is_a175':book['source_sha']==q.A175_FAILED_CANDIDATE,
            'real_orders_sent':book['real_orders_sent'],
            'admission':admission,'critical_capability':capability}


def prove(candidate,tree,policy_b64,required):
    if (candidate != EXPECTED_CANDIDATE or tree != EXPECTED_TREE or
            not GIT_SHA.fullmatch(candidate) or not GIT_SHA.fullmatch(tree) or
            datetime.now(ZoneInfo('America/Argentina/Buenos_Aires'))
               .date().isoformat()!=DAY):
        raise ValueError('A178_CANDIDATE_OR_DAY_CHANGED')
    droplet=metadata()
    fd=read_lock()
    try:
        q=stage(STAGED_Q_GZIP_B64,STAGED_Q_SHA256,'a178_staged_quiescence',maximum=260000)
        s=stage(STAGED_S_GZIP_B64,STAGED_S_SHA256,'a178_staged_scheduler',maximum=130000)
        if (q.A176_OPERATOR_NO_GO_RECEIPT_SHA256 != EXPECTED_SEAL or
                q.A175_FAILED_CANDIDATE != '18ba517a846c6798f0d12f39072465fc02fa03b5'):
            raise ValueError('A178_RECEIPT_PIN_RED')
        # This env is transient to the proof process, not installed or saved.
        os.environ['RC6_A145_QUIESCENCE_GZIP_B64']=STAGED_Q_GZIP_B64
        daily=s.exact_a176_operator_no_go_continuation()
        if (daily.get('receipt_sha256') != EXPECTED_SEAL or
                daily.get('status') != 'NO_GO_PENDING_EXPLICIT_REARM' or
                daily.get('paper_execution_authorized') is not False):
            raise ValueError('A178_DAILY_SEAL_RED')
        origin_book=q.rescue_flat_book()
        intraday=(origin_book['process_state'],origin_book['session_state'])==(
            'RUNNING','MARKET_OPEN')
        try:
            origin=q.a175_failed_running_proof(
                candidate, operator_a151_proof=s.exact_a151_operator_preopen_pause,
                operator_timer_disabled=s.operator_preopen_timer_disabled,
                operator_current_no_go_proof=s.exact_a176_operator_no_go_continuation,
                require_installed_a175_source=True,
                allow_intraday_a175=intraday)
        except ValueError as exc:
            if str(exc) != 'QUIESCENCE_A175_RUNNING_BOOK_CHANGED':
                raise
            raise ValueError(str(exc)+':'+json.dumps(
                diagnose_origin_book(q),sort_keys=True)) from exc
        if (origin.get('origin_candidate') != q.A175_FAILED_CANDIDATE or
                origin.get('operator_receipt_sha256') != EXPECTED_SEAL or
                origin.get('flat_book',{}).get('real_orders_sent') != 0 or
                origin.get('flat_book') != origin_book or
                origin.get('intraday_a175') is not intraday or
                origin.get('image_id') != ACTIVE_IMAGE):
            raise ValueError('A178_A175_ORIGIN_CHANGED')
        disk=capacity(required,policy_b64)
        timers=timer_hold(s)
        images=read_docker_images()
        if (BARRIER.is_symlink() or BARRIER.exists()):
            raise ValueError('A178_BARRIER_CHANGED_DURING_PROOF')
        return {'schema':'rc6.a178.a175-running-frozen-preflight.v1',
                'status':'GREEN_READ_ONLY','at_utc':datetime.now(timezone.utc).isoformat(),
                'candidate_sha':candidate,'candidate_tree':tree,
                'droplet':droplet,'operator_receipt_sha256':EXPECTED_SEAL,
                'a175_origin_sha256':origin['origin_sha256'],
                'a175_session':origin_book['session_state'],
                'images_protected':sorted(images),
                'timers':timers,'disk':disk,'paper':{'mode':'PRODUCTION_PAPER',
                'real_orders_sent':0,'operational_go':False},
                'host_mutations':0,'provider_calls':0,'ppi_watch_mutations':0}
    finally:
        os.close(fd)


def main():
    result={'schema':'rc6.a178.a175-running-frozen-preflight.v1',
            'status':'RED','host_mutations':0,'operational_go':False}
    try:
        if len(sys.argv)!=5:
            raise ValueError('A178_ARGUMENTS_INVALID')
        result=prove(sys.argv[1],sys.argv[2],sys.argv[3],int(sys.argv[4]))
    except (ValueError,KeyError,TypeError,AttributeError,OSError,subprocess.SubprocessError,
            UnicodeError,OverflowError) as exc:
        result['reason']=(str(exc) if isinstance(exc,ValueError) else type(exc).__name__)
    print('RC6_A178_RUNNING_PREFLIGHT_RO='+json.dumps(result,sort_keys=True))
    return 0 if result['status']=='GREEN_READ_ONLY' else 2


if __name__=='__main__':
    raise SystemExit(main())
