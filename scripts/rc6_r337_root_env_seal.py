"""R337 CODE ONLY: tested internal transaction mechanics, NOT host authority.

Public execution always HOLD79. No approved independent root transport exists;
no callable public sealer, sudo, SSH, service or Docker action is provided.
Private fd functions are for controlled fixtures/future reviewed integration,
not a permission boundary against a caller already capable of running root.
R334 is hash-pinned and reused, never vendored or reimplemented. The final
combined candidate, physical custody and independent GO remain NO_VERIFICADO.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import ctypes
import errno
import fcntl
import hashlib
import hmac
import json
import os
import re
import stat
import types

_ROOT_UID = 0
_R334_SHA256 = 'c7060a643b0a72ee6b0416fd38967c5c741abe340667b8988e79dd3768f12fb8'
LEGACY_DIRECTORY = '/opt/porota-trading/data/diagnosticos'
GENERATIONS_DIRECTORY = '/var/lib/porota-rc6-oci-custody/generations'
_LEGACY_NAMES = {'dashboard': 'dashboard_mode_v17.env',
                 'observer': 'observer_runtime_v17.env'}
_GO_FIELDS = frozenset({'schema', 'action', 'candidate_sha', 'candidate_tree',
    'generation', 'nonce', 'issued_at', 'expires_at', 'normalizer_sha256',
    'sealer_sha256', 'source_sha256', 'output_sha256', 'paper_mode',
    'real_orders_allowed'})


class SealHold(RuntimeError):
    exit_code = 79

    def __init__(self, reason):
        if type(reason) is not str or not re.fullmatch('[A-Z][A-Z0-9_]{0,70}', reason):
            reason = 'UNVERIFIED'
        super().__init__('RC6_R337_HOLD79:' + reason)


def _require(ok, reason):
    if not ok:
        raise SealHold(reason)


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _hex(value, size):
    return type(value) is str and re.fullmatch('[0-9a-f]{' + str(size) + '}', value) is not None


def _clock(value):
    _require(type(value) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', value), 'INTENT_CLOCK')
    try:
        return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except ValueError:
        raise SealHold('INTENT_CLOCK') from None


@dataclass(frozen=True, repr=False)
class _Intent:
    generation: str
    nonce: str
    candidate_sha: str
    candidate_tree: str
    source_digests: tuple
    output_digests: tuple
    host_go: bool = field(default=False, init=False)


def _validate_intent(raw, *, intent_sha256, candidate_sha, candidate_tree,
                     nonce, sealer_sha256, now):
    """Pure consistency validation; caller pins are NOT independent root GO."""
    _require(type(raw) is bytes and 0 < len(raw) <= 8192 and
             _hex(intent_sha256, 64) and hmac.compare_digest(_digest(raw), intent_sha256), 'INTENT_PIN')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, 'INTENT_DUPLICATE')
            result[key] = value
        return result
    try:
        doc = json.loads(raw.decode('utf-8', 'strict'), object_pairs_hook=unique,
                         parse_constant=lambda value: (_ for _ in ()).throw(SealHold('INTENT_JSON')))
    except (ValueError, UnicodeError, RecursionError):
        raise SealHold('INTENT_JSON') from None
    _require(type(doc) is dict and set(doc) == _GO_FIELDS and type(doc['schema']) is int and
             doc['schema'] == 1 and doc['action'] == 'SEAL_ENV_PAIR', 'INTENT_SCHEMA')
    _require(_hex(candidate_sha, 40) and _hex(candidate_tree, 40) and _hex(nonce, 32) and
             _hex(sealer_sha256, 64) and doc['candidate_sha'] == candidate_sha and
             doc['candidate_tree'] == candidate_tree and doc['nonce'] == nonce and
             doc['sealer_sha256'] == sealer_sha256 and _hex(doc['generation'], 32) and
             doc['normalizer_sha256'] == _R334_SHA256, 'INTENT_IDENTITY')
    _require(doc['paper_mode'] == 'PRODUCTION_PAPER' and doc['real_orders_allowed'] is False,
             'INTENT_PAPER_ZERO')
    start, end, current = _clock(doc['issued_at']), _clock(doc['expires_at']), _clock(now)
    _require(start <= current < end and 0 < (end - start).total_seconds() <= 600, 'INTENT_EXPIRED')
    for key in ('source_sha256', 'output_sha256'):
        _require(type(doc[key]) is dict and set(doc[key]) == {'dashboard', 'observer'} and
                 all(_hex(value, 64) for value in doc[key].values()), 'INTENT_PAIR_DIGESTS')
    return _Intent(doc['generation'], nonce, candidate_sha, candidate_tree,
        tuple(doc['source_sha256'][role] for role in ('dashboard', 'observer')),
        tuple(doc['output_sha256'][role] for role in ('dashboard', 'observer')))


def _flags(directory=False):
    _require(hasattr(os, 'O_NOFOLLOW') and os.O_NOFOLLOW != 0, 'NOFOLLOW_UNAVAILABLE')
    return os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | (os.O_DIRECTORY if directory else os.O_NONBLOCK)


def _protected(inode, directory=False):
    _require(inode.st_uid == _ROOT_UID and not inode.st_mode & 0o022 and
             (stat.S_ISDIR(inode.st_mode) if directory else
              stat.S_ISREG(inode.st_mode) and inode.st_nlink == 1), 'INODE_CUSTODY')


def _open_physical_directory(path):
    _require(type(path) is str and path.startswith('/') and
             all(part not in ('', '.', '..') for part in path.split('/')[1:]), 'ABSOLUTE_PHYSICAL_PATH')
    fd = os.open('/', _flags(True))
    try:
        _protected(os.fstat(fd), True)
        for part in path.split('/')[1:]:
            next_fd = os.open(part, _flags(True), dir_fd=fd)
            os.close(fd)
            fd = next_fd
            _protected(os.fstat(fd), True)
        return fd
    except (OSError, SealHold):
        os.close(fd)
        raise SealHold('ANCESTOR_CUSTODY') from None


def _read_leaf(directory_fd, name, *, pin, mode=None):
    try:
        _protected(os.fstat(directory_fd), True)
        fd = os.open(name, _flags(), dir_fd=directory_fd)
        try:
            before = os.fstat(fd)
            _protected(before)
            _require(mode is None or stat.S_IMODE(before.st_mode) == mode, 'SOURCE_MODE')
            _require(0 < before.st_size <= 65536, 'SOURCE_SIZE')
            raw = os.read(fd, 65537)
            after = os.fstat(fd)
            identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            _require(identity(before) == identity(after) and len(raw) == before.st_size,
                     'SOURCE_CHANGED')
            named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            _require(identity(named) == identity(after), 'SOURCE_REPLACED')
            _require(_hex(pin, 64) and hmac.compare_digest(_digest(raw), pin), 'SOURCE_DIGEST')
            return raw
        finally:
            os.close(fd)
    except OSError:
        raise SealHold('SOURCE_CUSTODY') from None


def _read_legacy(directory_fd, role, pin):
    _require(type(role) is str and role in _LEGACY_NAMES, 'SOURCE_ROLE')
    return _read_leaf(directory_fd, _LEGACY_NAMES[role], pin=pin, mode=0o600)


def _load_pinned_r334(code_directory_fd):
    raw = _read_leaf(code_directory_fd, 'rc6_r334_root_env_genesis.py', pin=_R334_SHA256)
    module = types.ModuleType('_rc6_r337_pinned_r334')
    # dataclasses inspect their defining module; no unchecked source execution.
    import sys
    previous = sys.modules.get(module.__name__)
    sys.modules[module.__name__] = module
    try:
        exec(compile(raw, '<pinned-r334>', 'exec'), module.__dict__)
        return module
    except Exception:
        raise SealHold('NORMALIZER_IMPORT') from None
    finally:
        if previous is None:
            sys.modules.pop(module.__name__, None)
        else:
            sys.modules[module.__name__] = previous


def _propose_fixed_sources(legacy_directory_fd, code_directory_fd, intent):
    _require(type(intent) is _Intent, 'INTENT_REQUIRED')
    normalizer = _load_pinned_r334(code_directory_fd)
    sources = [_read_legacy(legacy_directory_fd, role, pin) for role, pin in
               zip(('dashboard', 'observer'), intent.source_digests)]
    try:
        proposal = normalizer.propose_env_pair(dashboard_source=sources[0], observer_source=sources[1],
            dashboard_source_sha256=intent.source_digests[0], observer_source_sha256=intent.source_digests[1])
    except Exception:
        raise SealHold('NORMALIZER_REJECTED') from None
    _require((proposal.dashboard.sha256, proposal.observer.sha256) == intent.output_digests,
             'APPROVED_OUTPUT_MISMATCH')
    return proposal


def _write_exclusive(directory_fd, name, raw):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(name, flags, 0o400, dir_fd=directory_fd)
    try:
        os.fchmod(fd, 0o400)
        position = 0
        while position < len(raw):
            count = os.write(fd, raw[position:])
            _require(count > 0, 'SHORT_WRITE')
            position += count
        os.fsync(fd)
    finally:
        os.close(fd)


def _rename_no_replace(parent_fd, source, target):
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    _require(rename is not None, 'ATOMIC_NOREPLACE_UNAVAILABLE')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(parent_fd, source.encode('ascii'), parent_fd, target.encode('ascii'), 1) != 0:
        raise OSError(ctypes.get_errno(), 'atomic no-replace failed')


@dataclass(frozen=True)
class _StageResult:
    host_go: bool = field(default=False, init=False)
    deploy_authorized: bool = field(default=False, init=False)
    r271_semantics: str = 'NO_VERIFICADO'


def _stage_pair(generations_fd, intent, dashboard_payload, observer_payload):
    """Fixture-testable mechanics only; never exposed by the hard-HOLD CLI.

    A completed generation is atomic/no-replace. An fsync failure after publish
    leaves that generation intact (fix-forward), never deletes published data.
    R271 semantic acceptance must be independently checked in a combined build.
    """
    _require(type(intent) is _Intent and _hex(intent.generation, 32) and _hex(intent.nonce, 32), 'INTENT_REQUIRED')
    payloads = (dashboard_payload, observer_payload)
    _require(all(type(raw) is bytes and 0 < len(raw) <= 65536 for raw in payloads) and
             tuple(_digest(raw) for raw in payloads) == intent.output_digests, 'PAIR_DIGEST')
    stage = '.r337-' + intent.generation + '-' + intent.nonce
    lock_fd = stage_fd = env_fd = None
    created = published = False
    stage_identity = None
    try:
        _protected(os.fstat(generations_fd), True)
        lock_fd = os.open('.', _flags(True), dir_fd=generations_fd)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.mkdir(stage, 0o700, dir_fd=generations_fd)
        created = True
        stage_fd = os.open(stage, _flags(True), dir_fd=generations_fd)
        stage_identity = os.fstat(stage_fd).st_ino
        os.fchmod(stage_fd, 0o700)
        _protected(os.fstat(stage_fd), True)
        os.mkdir('env', 0o700, dir_fd=stage_fd)
        env_fd = os.open('env', _flags(True), dir_fd=stage_fd)
        os.fchmod(env_fd, 0o700)
        for name, raw in zip(('dashboard.env', 'observer.env'), payloads):
            _write_exclusive(env_fd, name, raw)
        os.fsync(env_fd)
        receipt = dict(schema=1, candidate_sha=intent.candidate_sha, candidate_tree=intent.candidate_tree,
            generation=intent.generation, nonce=intent.nonce, host_go=False,
            r271_semantics='NO_VERIFICADO', source_sha256=intent.source_digests,
            output_sha256=intent.output_digests)
        _write_exclusive(stage_fd, 'receipt.json',
            (json.dumps(receipt, sort_keys=True, separators=(',', ':')) + '\n').encode('ascii'))
        os.fsync(stage_fd)
        named = os.stat(stage, dir_fd=generations_fd, follow_symlinks=False)
        _require(named.st_ino == stage_identity, 'STAGE_REPLACED')
        _rename_no_replace(generations_fd, stage, intent.generation)
        published = True
        os.fsync(generations_fd)
        return _StageResult()
    except (OSError, SealHold):
        raise SealHold('TRANSACTION_NOT_COMMITTED') from None
    finally:
        # Only names inside our newly created, same-inode stage are eligible.
        # Never overwrite/clean a pre-existing stage or a published generation.
        if created and not published and stage_fd is not None:
            try:
                named = os.stat(stage, dir_fd=generations_fd, follow_symlinks=False)
                if named.st_ino == stage_identity and stat.S_ISDIR(named.st_mode):
                    if env_fd is not None:
                        for name in ('dashboard.env', 'observer.env'):
                            try:
                                os.unlink(name, dir_fd=env_fd)
                            except FileNotFoundError:
                                pass
                        os.rmdir('env', dir_fd=stage_fd)
                    try:
                        os.unlink('receipt.json', dir_fd=stage_fd)
                    except FileNotFoundError:
                        pass
                    os.rmdir(stage, dir_fd=generations_fd)
                    os.fsync(generations_fd)
            except OSError:
                pass  # Incomplete stage retained; no broad cleanup or rollback.
        for fd in (env_fd, stage_fd, lock_fd):
            if fd is not None:
                os.close(fd)


def main(argv=None):
    """No trusted transport/independent authority exists: always NO_GO/HOLD79."""
    return 79


if __name__ == '__main__':
    raise SystemExit(main())
