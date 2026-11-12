#!/usr/bin/env python3
"""Deploy V2 private-owner flock and fail-closed INFLIGHT barriers."""
from __future__ import annotations

import fcntl
import ctypes
import errno
import hashlib
import json
import os
import pwd
import re
import secrets
import stat
import subprocess
import sys
import time
from pathlib import Path


LOCK_DIR = Path('/run/lock')
STAGING_DIR = Path('/tmp')
LOCK_PATH = LOCK_DIR / 'porota-rc6-deploy-v2.lock'
BARRIER_NAME = 'porota-rc6-deploy-v2-attempt.json'
PHASES = frozenset({
    'reconcile',
    'classify', 'offline_preclean', 'preclean', 'suspend', 'drain', 'transfer',
    'promotion', 'flat_book', 'verify_carryover', 'timers_restore',
    'timers_restored', 'backup_retire', 'finalize', 'cleanup_contract',
    'cleanup_recover', 'cleanup_admin_resume', 'cleanup_restore',
    'cleanup_payload', 'cleanup_final',
    'cleanup_presuspend',
})
FINAL_PHASES = frozenset({'finalize', 'cleanup_final', 'cleanup_presuspend'})
IDENTITY = re.compile(r'[0-9a-f]{40}')
NUMBER = re.compile(r'[1-9][0-9]*')
PR_SET_CHILD_SUBREAPER = 36
FAILED_A138_CANDIDATE = 'dd22a1b0761cc4f231813edb6b502d1dd21bb8de'
FAILED_A138_RUN = '40592426296'
FAILED_A138_ATTEMPT = '2'
FAILED_A138_SOURCE_SHA256 = 'dcb0ed996a6be9aefb0a609139285f6160248909ccde92f189a24b77fb617662'
FAILED_A92_IMAGE_ID = 'sha256:586464c20b74ab3a7ba842df8dfafcd76f21eb4c7bed553f23f6f187924ff735'


def _verify_readonly_witness(fd: int, directory_path: Path,
                             trusted_uid: int) -> None:
    """Reject owner-FD inheritance, inode aliases and nonexclusive locks."""
    lock_path = directory_path / LOCK_PATH.name
    try:
        parent = os.lstat(directory_path)
        before = os.fstat(fd)
        path = os.lstat(lock_path)
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    except OSError as exc:
        raise RuntimeError('MUTEX_CONSUMER_WITNESS_UNSAFE') from exc
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != trusted_uid or
            parent.st_mode & (stat.S_IWGRP | stat.S_IWOTH) and
            not parent.st_mode & stat.S_ISVTX or
            not stat.S_ISREG(before.st_mode) or
            not stat.S_ISREG(path.st_mode) or
            before.st_uid != trusted_uid or path.st_uid != trusted_uid or
            stat.S_IMODE(before.st_mode) != 0o600 or
            stat.S_IMODE(path.st_mode) != 0o600 or
            before.st_nlink != 1 or path.st_nlink != 1 or
            (before.st_dev, before.st_ino) != (path.st_dev, path.st_ino)):
        raise RuntimeError('MUTEX_CONSUMER_WITNESS_UNSAFE')
    if flags & os.O_ACCMODE != os.O_RDONLY:
        raise RuntimeError('MUTEX_CONSUMER_WITNESS_NOT_READONLY')
    # Both EX and SH must conflict; a shared owner blocks EX alone.
    for mode in (fcntl.LOCK_EX, fcntl.LOCK_SH):
        try:
            fcntl.flock(fd, mode | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                raise RuntimeError('MUTEX_CONSUMER_WITNESS_LOCK_UNVERIFIED') from exc
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
            raise RuntimeError('MUTEX_CONSUMER_OWNER_LOCK_ABSENT')
    try:
        after = os.lstat(lock_path)
        opened = os.fstat(fd)
    except OSError as exc:
        raise RuntimeError('MUTEX_CONSUMER_WITNESS_UNSAFE') from exc
    if ((after.st_dev, after.st_ino) != (before.st_dev, before.st_ino) or
            (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino) or
            after.st_nlink != 1):
        raise RuntimeError('MUTEX_CONSUMER_WITNESS_UNSAFE')


def require_migrated_consumer_protocol(*, trusted_uid: int = 0) -> None:
    raw = os.environ.get('RC6_DEPLOY_MUTEX_FD', '')
    if not re.fullmatch(r'[1-9][0-9]{0,7}', raw):
        raise RuntimeError('MUTEX_CONSUMER_WITNESS_REQUIRED')
    _verify_readonly_witness(int(raw), LOCK_DIR, trusted_uid)


def _open_consumer_witness(owner_fd: int, directory_path: Path,
                           *, trusted_uid: int = 0) -> int:
    lock_path = directory_path / LOCK_PATH.name
    try:
        owner = os.fstat(owner_fd)
        path = os.lstat(lock_path)
    except OSError as exc:
        raise RuntimeError('MUTEX_CONSUMER_OWNER_UNSAFE') from exc
    if (not stat.S_ISREG(owner.st_mode) or owner.st_uid != trusted_uid or
            stat.S_IMODE(owner.st_mode) != 0o600 or owner.st_nlink != 1 or
            (owner.st_dev, owner.st_ino) != (path.st_dev, path.st_ino)):
        raise RuntimeError('MUTEX_CONSUMER_OWNER_UNSAFE')
    try:
        witness = os.open(lock_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise RuntimeError('MUTEX_CONSUMER_WITNESS_UNSAFE') from exc
    try:
        _verify_readonly_witness(witness, directory_path, trusted_uid)
        return witness
    except BaseException:
        os.close(witness)
        raise


def open_consumer_witness(owner_fd: int, directory_path: Path) -> int:
    return _open_consumer_witness(owner_fd, directory_path, trusted_uid=0)


def require_exclusive_docker_namespace() -> None:
    raise RuntimeError('DOCKER_NAMESPACE_EXCLUSIVITY_UNVERIFIED')


def require_readonly_docker_command(args: tuple) -> None:
    if args and (args[0] in {'ps', 'inspect'} or
                 args[:2] in {('image', 'inspect'), ('image', 'ls'),
                              ('system', 'df')}):
        return
    raise RuntimeError('DOCKER_MUTATION_UNAUTHORIZED')


def open_lock(directory_path: Path = LOCK_DIR, trusted_uid: int = 0) -> int:
    lock_path = directory_path / LOCK_PATH.name
    directory = os.lstat(directory_path)
    if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != trusted_uid or
            directory.st_mode & (stat.S_IWGRP | stat.S_IWOTH) and
            not directory.st_mode & stat.S_ISVTX):
        raise RuntimeError('MUTEX_PARENT_UNSAFE')
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        before = os.fstat(fd)
        on_path = os.lstat(lock_path)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != trusted_uid or
                stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1 or
                (before.st_dev, before.st_ino) != (on_path.st_dev, on_path.st_ino)):
            raise RuntimeError('MUTEX_ALIAS_UNSAFE')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('MUTEX_BUSY') from exc
        after = os.lstat(lock_path)
        if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
            raise RuntimeError('MUTEX_REPLACED')
        return fd
    except BaseException:
        os.close(fd)
        raise


def _barrier(directory_path: Path) -> Path:
    return directory_path / BARRIER_NAME


def _read_barrier(directory_path: Path, trusted_uid: int = 0) -> dict | None:
    path = _barrier(directory_path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    try:
        before, on_path = os.fstat(fd), os.lstat(path)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != trusted_uid or
                stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1 or
                (before.st_dev, before.st_ino) != (on_path.st_dev, on_path.st_ino) or
                before.st_size > 1024):
            raise RuntimeError('BARRIER_ALIAS_UNSAFE')
        data = os.read(fd, 1025)
        after = os.lstat(path)
        if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
            raise RuntimeError('BARRIER_REPLACED')
        if len(data) > 1024:
            raise RuntimeError('BARRIER_INVALID')
        try:
            state = json.loads(data)
        except (UnicodeError, ValueError) as exc:
            raise RuntimeError('BARRIER_INVALID') from exc
        if (not isinstance(state, dict) or set(state) !=
                {'candidate', 'run', 'attempt', 'phase', 'status'} or
                not isinstance(state['candidate'], str) or
                not IDENTITY.fullmatch(state['candidate']) or
                not isinstance(state['run'], str) or not NUMBER.fullmatch(state['run']) or
                not isinstance(state['attempt'], str) or not NUMBER.fullmatch(state['attempt']) or
                not isinstance(state['phase'], str) or state['phase'] not in PHASES or
                not isinstance(state['status'], str) or
                state['status'] not in {'IDLE', 'INFLIGHT', 'CONTRACT_ONLY'}):
            raise RuntimeError('BARRIER_INVALID')
        return state
    finally:
        os.close(fd)


def _sync_directory(directory_path: Path) -> None:
    fd = os.open(directory_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_barrier(directory_path: Path, state: dict) -> None:
    path = _barrier(directory_path)
    temp = directory_path / ('.' + BARRIER_NAME + '.' + secrets.token_hex(12))
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write((json.dumps(state, sort_keys=True, separators=(',', ':')) + '\n').encode())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        _sync_directory(directory_path)
    finally:
        if temp.exists():
            temp.unlink()


def _enter(directory_path: Path, identity: tuple[str, str, str], phase: str) -> None:
    state = _read_barrier(directory_path)
    if state is not None:
        if tuple(state[k] for k in ('candidate', 'run', 'attempt')) != identity:
            raise RuntimeError('BARRIER_OTHER_ATTEMPT')
        if state['status'] != 'IDLE' and not (
                state['status'] == 'CONTRACT_ONLY' and phase == 'cleanup_contract'):
            raise RuntimeError('BARRIER_PRIOR_OPERATION_UNCERTAIN')
    _write_barrier(directory_path, dict(zip(('candidate', 'run', 'attempt'), identity),
                                       phase=phase, status='INFLIGHT'))


def _finish(directory_path: Path, identity: tuple[str, str, str], phase: str,
            complete: bool) -> None:
    state = _read_barrier(directory_path)
    if (state is None or tuple(state[k] for k in ('candidate', 'run', 'attempt')) != identity or
            state['phase'] != phase or state['status'] != 'INFLIGHT'):
        raise RuntimeError('BARRIER_CHANGED_DURING_OPERATION')
    if complete:
        _barrier(directory_path).unlink()
        _sync_directory(directory_path)
    else:
        state['status'] = 'IDLE'
        _write_barrier(directory_path, state)


def _contract_only(directory_path: Path, identity: tuple[str, str, str], phase: str) -> None:
    state = _read_barrier(directory_path)
    if (state is None or tuple(state[k] for k in ('candidate', 'run', 'attempt')) != identity or
            state['phase'] != phase or state['status'] != 'INFLIGHT'):
        raise RuntimeError('BARRIER_CHANGED_DURING_OPERATION')
    state['status'] = 'CONTRACT_ONLY'
    _write_barrier(directory_path, state)


def _enable_subreaper() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), 'MUTEX_SUBREAPER_UNAVAILABLE')


def _drain_children(max_seconds: float = 10) -> None:
    deadline = time.monotonic() + max_seconds
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid:
            continue
        if time.monotonic() >= deadline:
            raise RuntimeError('MUTEX_DESCENDANT_STILL_RUNNING')
        time.sleep(.05)


def _contract_writers() -> set[str]:
    inventory = subprocess.run(
        ['docker', 'ps', '--format', '{{.Names}}', '--filter',
         'name=porota-deploy-contract-'], capture_output=True,
        text=True, timeout=10, check=False)
    if inventory.returncode:
        raise RuntimeError('CONTRACT_WRITER_INVENTORY_FAILED')
    names = set(inventory.stdout.splitlines())
    if any(not re.fullmatch(r'porota-deploy-contract-[0-9a-f]{40}', name)
           for name in names):
        raise RuntimeError('CONTRACT_WRITER_NAME_INVALID')
    return names


def _exact_a138_stopped_host_proof() -> str:
    """Proof for exact A138 stopped/barrier cleanup; never infer healthy."""
    root = Path('/opt/porota-trading')
    script = root / 'scripts/rc6_deploy_quiescence.py'
    if (script.is_symlink() or not script.is_file() or
            script.stat().st_size != 111053 or script.stat().st_nlink != 1 or
            hashlib.sha256(script.read_bytes()).hexdigest() != FAILED_A138_SOURCE_SHA256):
        raise RuntimeError('BARRIER_A138_HOST_SOURCE_CHANGED')
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from scripts import rc6_deploy_quiescence as q

    prior_path = root / 'data/deploy' / ('quiescence-' + FAILED_A138_CANDIDATE + '.json')
    if (prior_path.is_symlink() or not prior_path.is_file() or
            prior_path.stat().st_size > 65536 or prior_path.stat().st_nlink != 1):
        raise RuntimeError('BARRIER_A138_PRIOR_RECEIPT_UNSAFE')
    raw = prior_path.read_bytes()
    prior = json.loads(raw)
    if (prior.get('candidate_sha') != FAILED_A138_CANDIDATE or
            prior.get('status') not in {'HOST_INSTALLATION_STARTED',
                                        'FIX_FORWARD_VALIDATION_REQUIRED'} or
            prior.get('installation_started') is not True or
            prior.get('degraded_closed_rescue') is not True or
            prior.get('candidate_runtime_initial') is not None or
            prior.get('candidate_runtime_after_failure') is not None or
            not isinstance(prior.get('candidate_image_intent'), dict)):
        raise RuntimeError('BARRIER_A138_INSTALLATION_PHASE_CHANGED')
    q.exact_rescue_receipt(prior)
    q.exact_clean_stopped(prior)
    if (q.exact_a92_marker().get('candidate_sha') != q.RESCUE_SOURCE_SHA or
            q.admit(stopped=True, allow_degraded_closed_rescue=True,
                    stopped_receipt=prior) != 'STOPPED'):
        raise RuntimeError('BARRIER_A138_STOPPED_PAPER_CHANGED')
    for name in q.OWNED:
        now = q.inspect(name)
        if (now['Id'] != prior['containers'][name]['id'] or
                now['Image'] != FAILED_A92_IMAGE_ID or
                now['State']['Status'] != 'exited' or
                q.lifecycle(now) != prior['stop_evidence'][name]['after']):
            raise RuntimeError('BARRIER_A138_ORIGINAL_CONTAINER_CHANGED')
    book = q.rescue_flat_book()
    if (book.get('real_orders_sent') != 0 or
            (book.get('process_state'), book.get('session_state')) not in
            {('STOPPED', 'BOOT_CALENDAR'), ('STOPPED', 'CLOSED')} or
            q.critical_admission() != prior.get('critical_admission')):
        raise RuntimeError('BARRIER_A138_BOOK_OR_CRITICAL_CHANGED')
    return hashlib.sha256(raw).hexdigest()


def _reconcile_exact_a138_idle(directory_path: Path, identity: tuple[str, str, str]) -> None:
    """A140: retire only an exactly proven clean A138 IDLE barrier."""
    existing = _read_barrier(directory_path)
    if existing is None:
        print('RC6_A140_BARRIER_RECONCILE=ALREADY_EMPTY', file=sys.stderr, flush=True)
        return
    expected = dict(candidate=FAILED_A138_CANDIDATE, run=FAILED_A138_RUN,
                    attempt=FAILED_A138_ATTEMPT)
    if (identity[0] == FAILED_A138_CANDIDATE or
            any(existing.get(k) != v for k, v in expected.items()) or
            existing['status'] != 'IDLE' or
            existing['phase'] not in {'promotion', 'cleanup_recover', 'cleanup_restore'}):
        raise RuntimeError('BARRIER_A138_NOT_EXACT_CLEAN_IDLE')
    if _contract_writers():
        raise RuntimeError('BARRIER_A138_CONTRACT_WRITER_ALIVE')
    digest = _exact_a138_stopped_host_proof()
    # Re-check the host evidence and barrier before retiring it.
    if _read_barrier(directory_path) != existing or _contract_writers():
        raise RuntimeError('BARRIER_A138_CHANGED_DURING_PROOF')
    _barrier(directory_path).unlink()
    _sync_directory(directory_path)
    print('RC6_A140_BARRIER_RECONCILE=GREEN|prior_candidate=' + FAILED_A138_CANDIDATE +
          '|prior_run=' + FAILED_A138_RUN + '|prior_attempt=' + FAILED_A138_ATTEMPT +
          '|prior_receipt_sha256=' + digest + '|previous_status=IDLE|PAPER=0',
          file=sys.stderr, flush=True)


def run(argv: list[str]) -> int:
    if (os.geteuid() != 0 or len(argv) < 9 or argv[1] not in ('user', 'root') or
            argv[2] != '--attempt' or argv[6] != '--phase'):
        raise RuntimeError('MUTEX_INVOCATION_INVALID')
    identity = tuple(argv[3:6])
    if (not IDENTITY.fullmatch(identity[0]) or
            not NUMBER.fullmatch(identity[1]) or not NUMBER.fullmatch(identity[2]) or
            argv[7] not in PHASES):
        raise RuntimeError('MUTEX_ATTEMPT_INVALID')
    phase = argv[7]
    observe = phase == 'classify'
    reconcile = phase == 'reconcile'
    presuspend = phase == 'cleanup_presuspend'
    user = pwd.getpwnam(argv[0])
    if user.pw_uid == 0:
        raise RuntimeError('MUTEX_REMOTE_USER_MUST_BE_UNPRIVILEGED')
    allow_contract_lifecycle = argv[8] == '--allow-contract-lifecycle'
    command = argv[9:] if allow_contract_lifecycle else argv[8:]
    if reconcile and (argv[1] != 'root' or command != ['--exact-a138-idle']):
        raise RuntimeError('BARRIER_RECONCILE_INVOCATION_INVALID')
    if not command:
        raise RuntimeError('MUTEX_COMMAND_MISSING')
    if (allow_contract_lifecycle != (phase == 'cleanup_contract') or
            (phase in FINAL_PHASES | {'classify', 'offline_preclean'} and argv[1] != 'root') or
            (phase == 'cleanup_contract' and argv[1] != 'user')):
        raise RuntimeError('MUTEX_PHASE_INVALID')
    if presuspend and (command[:2] != ['python3', '-'] or
                       '--expected-fingerprint' not in command or
                       '--policy-b64' not in command):
        raise RuntimeError('PRESUSPEND_PROOF_COMMAND_INVALID')
    fd = open_lock(LOCK_DIR)
    witness_fd = None
    try:
        # Only finalizer gets a read-only witness, never the private owner FD.
        if phase == 'finalize':
            witness_fd = open_consumer_witness(fd, LOCK_DIR)
        _enable_subreaper()
        if reconcile:
            _reconcile_exact_a138_idle(LOCK_DIR, identity)
            return 0
        existing = _read_barrier(LOCK_DIR)
        if presuspend:
            stage = STAGING_DIR / ('porota-deploy-v2-' + '-'.join(identity))
            if stage.exists() or stage.is_symlink():
                raise RuntimeError('PRESUSPEND_TRANSFER_STAGE_PRESENT')
        if presuspend and existing is not None:
            if (tuple(existing[k] for k in ('candidate', 'run', 'attempt')) != identity or
                    existing['status'] != 'IDLE' or existing['phase'] not in
                    {'preclean', 'offline_preclean', 'cleanup_presuspend'}):
                raise RuntimeError('PRESUSPEND_BARRIER_NOT_EXACT_IDLE')
        if observe and existing is not None:
            raise RuntimeError('BARRIER_PRIOR_ATTEMPT_UNRESOLVED')
        if existing is not None and not observe:
            if tuple(existing[k] for k in ('candidate', 'run', 'attempt')) != identity:
                raise RuntimeError('BARRIER_OTHER_ATTEMPT')
            if existing['status'] != 'IDLE' and not (
                    existing['status'] == 'CONTRACT_ONLY' and phase == 'cleanup_contract'):
                raise RuntimeError('BARRIER_PRIOR_OPERATION_UNCERTAIN')
        # An orphaned Docker contract must not outlive the attempt barrier.
        if not allow_contract_lifecycle:
            if _contract_writers():
                raise RuntimeError('CONTRACT_WRITER_ORPHAN_RUNNING')
        if not observe and not (presuspend and existing is None):
            _enter(LOCK_DIR, identity, phase)
        env = os.environ.copy()
        # Ambient legacy FD claims cannot reach the child.
        env.pop('RC6_DEPLOY_MUTEX_FD', None)
        env.pop('RC6_DEPLOY_MUTEX_PROOF', None)
        inherited = ()
        if witness_fd is not None:
            env['RC6_DEPLOY_MUTEX_FD'] = str(witness_fd)
            inherited = (witness_fd,)
        if argv[1] == 'user':
            env.update(HOME=user.pw_dir, USER=user.pw_name, LOGNAME=user.pw_name)
        def drop_privileges() -> None:
            if argv[1] == 'user':
                os.initgroups(user.pw_name, user.pw_gid)
                os.setgid(user.pw_gid)
                os.setuid(user.pw_uid)
        print('RC6_DEPLOY_HOST_MUTEX=HELD', file=sys.stderr, flush=True)
        result = subprocess.run(command, check=False, env=env, pass_fds=inherited,
                                preexec_fn=drop_privileges)
        _drain_children()
        writers = _contract_writers()
        if writers:
            # Admit only the exact attempt's contract-stop cleanup.
            if phase == 'promotion' and writers == {'porota-deploy-contract-' + identity[0]}:
                _contract_only(LOCK_DIR, identity, phase)
                raise RuntimeError('CONTRACT_WRITER_REQUIRES_EXACT_STOP')
            raise RuntimeError('CONTRACT_WRITER_ORPHAN_RUNNING')
        if not observe and not (presuspend and existing is None):
            _finish(LOCK_DIR, identity, phase,
                    result.returncode == 0 and phase in FINAL_PHASES)
        if presuspend and result.returncode == 0:
            print('RC6_PRE_SUSPEND_BARRIER=GREEN|exact_attempt=' +
                  ('RETIRED' if existing is not None else 'ALREADY_EMPTY') +
                  '|no_scheduler_restore=true', file=sys.stderr, flush=True)
        return result.returncode
    finally:
        if witness_fd is not None:
            os.close(witness_fd)
        os.close(fd)


def main() -> int:
    try:
        return run(sys.argv[1:])
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print('RC6_DEPLOY_HOST_MUTEX=RED|' + str(exc), file=sys.stderr)
        return 75


if __name__ == '__main__':
    raise SystemExit(main())
