"""Durable original audit inputs; one existing worker, bounded non-executable codec.

No business computation or DB access belongs here. A receipt attests actual
canonical bytes, and never advances the source clock of its immutable HOT cut.
"""
from dataclasses import dataclass
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import time
import uuid

from .audit_spool import CanonicalJSONStream
from . import persistence as p

EPOCH = 'rc6.audit-actual-values.v1'
FRAME = struct.Struct('!cI')
REFERENCE = struct.Struct('!QQ32s')
BLOCK, PIECE, PACK_LIMIT = 65536, 131072, 512 * 1024**2
RESERVE, DEADLINE = 2 * 1024**3, 30.0
ENCODER = json.JSONEncoder(sort_keys=True, separators=(',', ':'), default=str, allow_nan=False)
NAME = re.compile(r'(?:queue\.json|(?:manifest|receipt)-[a-f0-9]{32}\.json|[a-f0-9]{32}\.pack|\.capture-[a-f0-9]{32}\.tmp|\.write-[a-f0-9]{32}\.tmp)')
IDENT = re.compile('[a-f0-9]{32}')


def _native():
    native = p._native_json_bound
    if native is None or any(not callable(getattr(native, name, None)) for name in
            ('snapshot_context', 'snapshot_encode', 'snapshot_decode', 'snapshot_batch')):
        raise ValueError('SHADOW_COLD_NATIVE_CODEC_REQUIRED')
    return native


def _identity(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def _guard(root, *, memory=True):
    if shutil.disk_usage(root).free < RESERVE:
        raise ValueError('SHADOW_COLD_DISK_RESERVE_BREACHED')
    if memory:
        available = None
        with open('/proc/meminfo', encoding='ascii') as stream:
            for line in stream:
                if line.startswith('MemAvailable:'):
                    available = int(line.split()[1]) * 1024
                    break
        if available is None or available <= 128 * 1024**2:
            raise ValueError('SHADOW_COLD_HOST_MEMORY_RESERVE_BREACHED')


class Directory:
    """Anchor all private I/O to a checked directory fd, including rename."""
    def __init__(self, files, *, create=False):
        self.files, self.root = files, files.root / 'cold-audit'
        if create:
            if files.lock is None:
                raise ValueError('SHADOW_COLD_WRITER_REQUIRED')
            self.root.mkdir(mode=0o700, exist_ok=True)
        self.fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(self.fd)
        if (info.st_uid != os.getuid() and (create or os.getuid() != 0) or
                stat.S_IMODE(info.st_mode) & 0o077):
            os.close(self.fd)
            raise ValueError('SHADOW_COLD_PRIVATE_DIRECTORY_REQUIRED')
        self.identity = (info.st_dev, info.st_ino)
        self.owner_uid = info.st_uid

    def __enter__(self):
        return self

    def __exit__(self, *args):
        os.close(self.fd)

    def check(self, name, *, write=False):
        if not isinstance(name, str) or NAME.fullmatch(name) is None:
            raise ValueError('SHADOW_COLD_BASENAME_REQUIRED')
        info = self.root.lstat()
        if (not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != self.identity
                or info.st_uid != self.owner_uid or stat.S_IMODE(info.st_mode) & 0o077):
            raise ValueError('SHADOW_COLD_DIRECTORY_CHANGED')
        if write and self.files.lock is None:
            raise ValueError('SHADOW_COLD_WRITER_REQUIRED')
        return name

    def open(self, name, *, create=False):
        self.check(name, write=create)
        flags = os.O_NOFOLLOW | (os.O_RDWR | os.O_CREAT | os.O_EXCL if create else os.O_RDONLY)
        fd = os.open(name, flags, 0o600, dir_fd=self.fd)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                info.st_uid != self.owner_uid or stat.S_IMODE(info.st_mode) & 0o077):
            os.close(fd)
            raise ValueError('SHADOW_COLD_PRIVATE_FILE_REQUIRED')
        return os.fdopen(fd, 'w+b' if create else 'rb', buffering=0)

    def read(self, name):
        try:
            with self.open(name) as stream:
                raw = stream.read(256 * 1024 + 1)
        except FileNotFoundError:
            return None
        if len(raw) > 256 * 1024:
            raise ValueError('SHADOW_COLD_MANIFEST_BOUND')
        envelope = json.loads(raw)
        if (type(envelope) is not dict or set(envelope) != {'payload', 'sha256'} or
                not isinstance(envelope['payload'], dict) or
                p.digest(envelope['payload']) != envelope['sha256']):
            raise ValueError('SHADOW_COLD_MANIFEST_HASH')
        return envelope['payload']

    def write(self, name, payload):
        self.check(name, write=True)
        # Refuse a pre-existing alias even though rename itself is fd-anchored.
        try:
            with self.open(name):
                pass
        except FileNotFoundError:
            pass
        raw = ENCODER.encode({'payload': payload, 'sha256': p.digest(payload)}).encode('ascii')
        if len(raw) > 256 * 1024:
            raise ValueError('SHADOW_COLD_MANIFEST_BOUND')
        temp = '.write-' + uuid.uuid4().hex + '.tmp'
        try:
            with self.open(temp, create=True) as stream:
                _write_all(stream, raw)
                os.fsync(stream.fileno())
            os.replace(temp, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            os.fsync(self.fd)
        finally:
            try:
                os.unlink(temp, dir_fd=self.fd)
            except FileNotFoundError:
                pass

    def remove(self, name):
        self.check(name, write=True)
        with self.open(name):
            pass
        os.unlink(name, dir_fd=self.fd)
        os.fsync(self.fd)

    def entries(self):
        names = os.listdir(self.fd)
        if len(names) > 512:
            raise ValueError('SHADOW_COLD_FILE_COUNT_BOUND')
        result = []
        for name in names:
            self.check(name)
            with self.open(name) as stream:
                result.append((name, os.fstat(stream.fileno())))
        return result


def _write_all(stream, data):
    pending = memoryview(data)
    while pending:
        count = stream.write(pending)
        if not count:
            raise OSError('SHADOW_COLD_SHORT_WRITE')
        pending = pending[count:]


def _hash(stream, offset=0, length=None, *, guard=None):
    digest = hashlib.sha256()
    stream.seek(offset)
    length = os.fstat(stream.fileno()).st_size - offset if length is None else length
    while length:
        if guard:
            guard()
        chunk = stream.read(min(length, BLOCK))
        if not chunk:
            raise ValueError('SHADOW_COLD_TRUNCATED')
        digest.update(chunk)
        length -= len(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class SnapshotValue(CanonicalJSONStream):
    owner: object
    descriptor: dict

    def iter_bytes(self):
        yield from self.owner.expand(self.descriptor)


class Snapshot:
    """One cut's complete actual input, captured at original final-use sites."""
    def __init__(self, files, *, should_abort=None):
        if should_abort is not None and not callable(should_abort):
            raise ValueError('SHADOW_COLD_CANCEL_CALLBACK_INVALID')
        self.should_abort = should_abort or (lambda: False)
        self.files = files
        self.ident = uuid.uuid4().hex
        self.temp = '.capture-' + self.ident + '.tmp'
        self.pack = self.ident + '.pack'
        self.stream = self.directory = None
        self.profile, self.ranges = [], []
        self.total_bytes = self.last_sync = self.external_bytes = 0
        self.context = None
        self.active, self.pending = set(), bytearray()
        self.sealed = False

    def __enter__(self):
        self.codec = _native()  # Immutable process code, validated once per cut.
        self.directory = Directory(self.files, create=True)
        try:
            ensure_idle(self.files, directory=self.directory)
            if shutil.disk_usage(self.files.root).free < RESERVE + (512 + 64) * 1024**2:
                raise ValueError('SHADOW_COLD_DISK_ADMISSION_BLOCKED')
            # Anonymous until sealed: no mutable pathname can be copied or
            # mistaken for durable input by a concurrent filesystem reader.
            self.directory.check(self.pack, write=True)
            fd = os.open('.', os.O_RDWR | os.O_TMPFILE, 0o600, dir_fd=self.directory.fd)
            self.stream = os.fdopen(fd, 'w+b', buffering=0)
            info = os.fstat(fd)
            if info.st_nlink != 0 or not stat.S_ISREG(info.st_mode):
                raise ValueError('SHADOW_COLD_ANONYMOUS_CAPTURE_REQUIRED')
            self.capture_identity = (info.st_dev, info.st_ino)
        except BaseException:
            if self.stream is not None:
                self.stream.close()
            self.directory.__exit__()
            raise
        return self

    def __exit__(self, *args):
        if self.stream is not None:
            self.stream.close()
        if self.directory is not None:
            if not self.sealed:
                # This input never acquired a durable manifest or a HOT cut.
                try:
                    self.directory.remove(self.temp)
                except FileNotFoundError:
                    pass
            self.directory.__exit__()

    def _sync(self):
        info = os.fstat(self.stream.fileno())
        if info.st_nlink != 0 or (info.st_dev, info.st_ino) != self.capture_identity:
            raise ValueError('SHADOW_COLD_CAPTURE_IDENTITY_CHANGED')
        os.fsync(self.stream.fileno())
        if hasattr(os, 'posix_fadvise'):
            os.posix_fadvise(self.stream.fileno(), self.last_sync,
                            self.total_bytes-self.last_sync, os.POSIX_FADV_DONTNEED)
        self.last_sync = self.total_bytes

    def _frame(self, kind, raw):
        if not 0 < len(raw) <= PIECE:
            raise ValueError('SHADOW_COLD_FRAME_BOUND')
        frame = FRAME.pack(kind, len(raw)) + hashlib.sha256(raw).digest() + raw
        if (type(self.external_bytes) is not int or not 0 <= self.external_bytes <= 64*1024**2
                or self.total_bytes + len(frame) > PACK_LIMIT):
            raise ValueError('SHADOW_COLD_PACK_QUOTA')
        if self.total_bytes-self.last_sync+len(frame) > 8*1024**2:
            if self.should_abort():
                raise ValueError('SHADOW_COLD_CAPTURE_CANCELLED')
            self._sync()
            _guard(self.files.root)
        if shutil.disk_usage(self.files.root).free < RESERVE + len(frame):
            raise ValueError('SHADOW_COLD_DISK_RESERVE_BREACHED')
        _write_all(self.stream, frame)
        self.hasher.update(frame)
        self.segment_hash.update(frame)
        self.total_bytes += len(frame)

    def _flush(self):
        if self.pending:
            raw = bytes(self.pending)
            self.pending.clear()
            self._frame(b'J', raw)

    def record(self, kind, raw):
        if kind == b'J':
            pending = memoryview(raw)
            while pending:
                count = min(BLOCK-len(self.pending), len(pending))
                self.pending.extend(pending[:count])
                pending = pending[count:]
                if len(self.pending) == BLOCK:
                    self._flush()
        else:
            self._flush()
            self._frame(kind, raw)

    def literal(self, value):
        for at in range(0, len(value), BLOCK):
            self.record(b'J', value[at:at+BLOCK].encode('ascii'))

    def value(self, value, depth=0):
        if depth > 128:
            raise ValueError('SHADOW_COLD_CAPTURE_DEPTH')
        if type(value) is SnapshotValue:
            desc = value.descriptor
            if value.owner is not self or desc not in self.ranges or desc['offset']+desc['length'] > self.offset:
                raise ValueError('SHADOW_COLD_REFERENCE_NOT_BACKWARD')
            self.record(b'R', REFERENCE.pack(desc['offset'], desc['length'], bytes.fromhex(desc['sha256'])))
            return
        captured = self.codec.snapshot_encode(value, self.context)
        if captured is not None:
            # Pay the canonical encoding cost while the original value is
            # still owned by HOT. The durable frame remains the exact ASCII
            # bytes of that value, so COLD need only verify/copy it. On the
            # current shared Droplet, decoding and re-encoding 12k native
            # frames consumed most of the original 30-second COLD budget.
            self.record(b'J', ENCODER.encode(value).encode('ascii'))
            return
        if not isinstance(value, (dict, list, tuple)):
            # Preserve stdlib default=str and oversized leaf semantics before
            # HOT publication; no arbitrary object is ever decoded in COLD.
            for chunk in p.canonical_ascii_chunks(value):
                self.record(b'J', chunk)
            return
        marker = id(value)
        if marker in self.active:
            raise ValueError('SHADOW_COLD_CAPTURE_CYCLE')
        self.active.add(marker)
        try:
            if isinstance(value, dict):
                self.literal('{')
                for index, key in enumerate(sorted(value)):
                    if index:
                        self.literal(',')
                    encoded_key = ENCODER.encode({key: None})
                    self.literal(encoded_key[1:-len(':null}')] + ':')
                    self.value(value[key], depth+1)
                self.literal('}')
            else:
                self.literal('[')
                offset = 0
                while offset < len(value):
                    group = list(value[offset:offset+16])
                    captured = self.codec.snapshot_batch(group, self.context)
                    if offset:
                        self.literal(',')
                    if captured is not None:
                        raw, consumed = captured
                        if type(consumed) is not int or not 1 <= consumed <= len(group):
                            raise ValueError('SHADOW_COLD_BATCH_PROGRESS_REQUIRED')
                        canonical = ENCODER.encode(group[:consumed]).encode('ascii')
                        self.record(b'J', canonical[1:-1])
                    else:
                        self.value(value[offset], depth+1)
                        consumed = 1
                    offset += consumed
                self.literal(']')
        finally:
            self.active.remove(marker)

    def stage(self, role, value):
        if self.should_abort():
            raise ValueError('SHADOW_COLD_CAPTURE_CANCELLED')
        if self.sealed or self.stream is None or self.stream.closed:
            raise ValueError('SHADOW_COLD_CAPTURE_ALREADY_SEALED_OR_CLOSED')
        if not re.fullmatch('[A-Za-z0-9_]{1,64}', role) or len(self.ranges) >= 64:
            raise ValueError('SHADOW_COLD_ROLE_BOUND')
        wall, cpu = time.monotonic(), time.process_time()
        if not self.ranges:
            self.hasher = hashlib.sha256()
        self.offset = self.total_bytes
        self.segment_hash = hashlib.sha256()
        self.context = self.codec.snapshot_context(p.FrozenJSONDict, p._FrozenEmptyList)
        self.value(value)
        self._flush()
        self._sync()
        desc = {'role': role, 'offset': self.offset, 'length': self.total_bytes-self.offset,
                'sha256': self.segment_hash.hexdigest()}
        self.ranges.append(desc)
        self.profile.append({'role': role, 'snapshot_bytes': desc['length'],
                             'wall_seconds': round(time.monotonic()-wall, 6),
                             'cpu_seconds': round(time.process_time()-cpu, 6)})
        return SnapshotValue(self, desc)

    def seal(self, payload, *, source_as_of, configuration):
        root = self.stage('full_computed_root', payload).descriptor
        self._sync()
        if self.should_abort():
            raise ValueError('SHADOW_COLD_CAPTURE_CANCELLED')
        # linkat(AT_SYMLINK_FOLLOW) through our checked fd publishes the SAME
        # O_TMPFILE inode. It cannot overwrite an existing target. /proc is
        # required explicitly; unsupported capability fails before CURRENT.
        self.directory.check(self.pack, write=True)
        self.directory.entries()  # Unknown namespace entries still fail closed.
        before = os.fstat(self.stream.fileno())
        os.link('/proc/self/fd/'+str(self.stream.fileno()), self.pack,
                dst_dir_fd=self.directory.fd, follow_symlinks=True)
        linked = os.stat(self.pack, dir_fd=self.directory.fd, follow_symlinks=False)
        if (linked.st_dev, linked.st_ino) != (before.st_dev, before.st_ino) or linked.st_nlink != 1:
            raise ValueError('SHADOW_COLD_ATOMIC_LINK_IDENTITY')
        os.fsync(self.directory.fd)
        self.stream.close()
        self.stream = None
        with self.directory.open(self.pack) as stream:
            identity = _identity(os.fstat(stream.fileno()))
        sealed_at = time.time()
        manifest = {'epoch': EPOCH, 'snapshot_id': self.ident, 'pack': self.pack,
                    'pack_identity': identity, 'pack_sha256': self.hasher.hexdigest(),
                    'pack_bytes': self.total_bytes, 'root': root, 'segments': self.ranges,
                    'source_as_of': source_as_of, 'configuration_fingerprint': configuration,
                    'sealed_at': sealed_at, 'deadline_at': sealed_at+DEADLINE}
        self.directory.write('manifest-'+self.ident+'.json', manifest)
        self.directory.write('queue.json', {'snapshot_id': self.ident, 'manifest_sha256': p.digest(manifest),
                                          'state': 'PENDING'})
        self.sealed = True
        return {'schema': 'rc6.computed-audit-reference.v2', 'state': 'PENDING',
                'snapshot_id': self.ident, 'manifest_sha256': p.digest(manifest), 'epoch': EPOCH,
                'source_as_of': source_as_of, 'configuration_fingerprint': configuration,
                'deadline_at': manifest['deadline_at']}

    def expand(self, descriptor):
        yield from _expand(self.directory, self.pack, descriptor, self.ranges,
                           source_fd=None if self.sealed else self.stream.fileno())


def _descriptor(desc, maximum):
    if (not isinstance(desc, dict) or type(desc.get('offset')) is not int or
            type(desc.get('length')) is not int or not 0 <= desc['offset'] < maximum or
            not 0 < desc['length'] <= maximum-desc['offset'] or
            not re.fullmatch('[a-f0-9]{64}', str(desc.get('sha256', '')))):
        raise ValueError('SHADOW_COLD_SEGMENT_BOUND')


class _Cursor:
    # pread keeps each recursive segment's position independent of the live
    # anonymous capture fd. dup/seek would share its open-file-description.
    def __init__(self, fd):
        self.fd, self.position = os.dup(fd), 0
    def __enter__(self): return self
    def __exit__(self, *args): os.close(self.fd)
    def fileno(self): return self.fd
    def seek(self, offset): self.position = offset
    def tell(self): return self.position
    def read(self, count):
        value = os.pread(self.fd, count, self.position)
        self.position += len(value)
        return value


def _expand(directory, name, desc, segments, *, depth=0, check=None,
            source_fd=None, pack_prevalidated=False):
    if depth > 32:
        raise ValueError('SHADOW_COLD_REFERENCE_DEPTH')
    with (directory.open(name) if source_fd is None else _Cursor(source_fd)) as stream:
        _descriptor(desc, os.fstat(stream.fileno()).st_size)
        # COLD has already hashed the *entire sealed pack* against its durable
        # manifest before opening the report temp file. Rehashing each segment
        # here rereads ~200 MiB while competing with gzip on the 1 GiB host.
        # HOT/unsealed expansion still verifies each segment independently.
        if not pack_prevalidated and _hash(stream, desc['offset'], desc['length'], guard=check) != desc['sha256']:
            raise ValueError('SHADOW_COLD_SEGMENT_HASH')
        stream.seek(desc['offset'])
        end = desc['offset']+desc['length']
        native = _native()
        context = native.snapshot_context()
        while stream.tell() < end:
            if check:
                check()
            header = stream.read(FRAME.size)
            if len(header) != FRAME.size:
                raise ValueError('SHADOW_COLD_HEADER_TRUNCATED')
            kind, size = FRAME.unpack(header)
            if not 0 < size <= PIECE or stream.tell()+32+size > end:
                raise ValueError('SHADOW_COLD_FRAME_BOUND')
            expected, raw = stream.read(32), stream.read(size)
            if hashlib.sha256(raw).digest() != expected:
                raise ValueError('SHADOW_COLD_FRAME_HASH')
            if kind == b'R':
                if size != REFERENCE.size:
                    raise ValueError('SHADOW_COLD_REFERENCE_SHAPE')
                start, count, checksum = REFERENCE.unpack(raw)
                child = {'offset': start, 'length': count, 'sha256': checksum.hex()}
                if start+count > desc['offset'] or not any(
                        all(s.get(k) == v for k, v in child.items()) for s in segments):
                    raise ValueError('SHADOW_COLD_REFERENCE_NOT_BACKWARD')
                yield from _expand(directory, name, child, segments, depth=depth+1,
                                   check=check, source_fd=source_fd,
                                   pack_prevalidated=pack_prevalidated)
            elif kind == b'J':
                if not raw.isascii():
                    raise ValueError('SHADOW_COLD_LITERAL_ASCII')
                yield raw
            elif kind in (b'P', b'A'):
                value = native.snapshot_decode(raw, context)
                if kind == b'A' and (type(value) is not list or len(value) > 16):
                    raise ValueError('SHADOW_COLD_BATCH_TYPE')
                encoded = ENCODER.encode(value).encode('ascii')
                yield encoded[1:-1] if kind == b'A' else encoded
            else:
                raise ValueError('SHADOW_COLD_FRAME_TYPE')


def _manifest(directory, reference):
    ident = reference.get('snapshot_id')
    if not isinstance(ident, str) or not IDENT.fullmatch(ident):
        raise ValueError('SHADOW_COLD_SNAPSHOT_ID')
    manifest = directory.read('manifest-'+ident+'.json')
    if (not isinstance(manifest, dict) or p.digest(manifest) != reference.get('manifest_sha256') or
            manifest.get('epoch') != EPOCH or reference.get('epoch', EPOCH) != EPOCH or
            manifest.get('snapshot_id') != ident or manifest.get('pack') != ident+'.pack' or
            type(manifest.get('pack_bytes')) is not int or not 0 < manifest['pack_bytes'] <= PACK_LIMIT or
            not isinstance(manifest.get('segments'), list) or not 1 <= len(manifest['segments']) <= 64 or
            manifest.get('root') != manifest['segments'][-1] or
            manifest.get('deadline_at') != manifest.get('sealed_at', 0)+DEADLINE):
        raise ValueError('SHADOW_COLD_MANIFEST_BINDING')
    for desc in manifest['segments']:
        _descriptor(desc, manifest['pack_bytes'])
    for key in ('source_as_of', 'configuration_fingerprint', 'deadline_at'):
        if key in reference and reference[key] != manifest[key]:
            raise ValueError('SHADOW_COLD_ORIGINAL_SOURCE_BINDING')
    return manifest


def _check_deadline(manifest):
    now = time.time()
    if not manifest['sealed_at'] <= now <= manifest['deadline_at']:
        raise ValueError('SHADOW_COLD_DEADLINE_EXPIRED_OR_CLOCK_REGRESSED')


def _pack_identity(directory, manifest):
    with directory.open(manifest['pack']) as stream:
        if _identity(os.fstat(stream.fileno())) != manifest['pack_identity']:
            raise ValueError('SHADOW_COLD_SEALED_INPUT_CHANGED')


def _report_identity(files, record):
    name = record.get('file', '')
    if (not isinstance(name, str) or not re.fullmatch('computed-[a-f0-9]{64}\\.json\\.gz', name) or
            name != 'computed-'+str(record.get('sha256'))+'.json.gz'):
        raise ValueError('SHADOW_COLD_COMPUTED_IDENTITY')
    path = files.path(name)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(fd)
        raise ValueError('SHADOW_COLD_COMPUTED_ALIAS')
    return os.fdopen(fd, 'rb'), _identity(info)


def _verified_report(files, record, check):
    stream, before = _report_identity(files, record)
    digest, count = hashlib.sha256(), 0
    with stream, gzip.GzipFile(fileobj=stream, mode='rb') as zipped:
        while chunk := zipped.read(BLOCK):
            check()
            count += len(chunk)
            if count > 512 * 1024**2:
                raise ValueError('SHADOW_COLD_COMPUTED_RAW_BOUND')
            digest.update(chunk)
        if _identity(os.fstat(stream.fileno())) != before:
            raise ValueError('SHADOW_COLD_COMPUTED_CHANGED_DURING_VERIFICATION')
    if digest.hexdigest() != record.get('sha256') or count != record.get('raw_bytes') or before[2] != record.get('compressed_bytes'):
        raise ValueError('SHADOW_COLD_COMPUTED_HASH')
    return before


def ensure_idle(files, *, directory=None):
    """Recover sealed-but-not-queued input, never silently discard a backlog."""
    if directory is None:
        with Directory(files, create=True) as own:
            return ensure_idle(files, directory=own)
    queue = directory.read('queue.json')
    entries = directory.entries()
    # Sealing manifest is durable before queue.json. Recover that crash gap.
    pending, failed = [], []
    for name, info in entries:
        if name.startswith('receipt-'):
            receipt = directory.read(name)
            if receipt.get('state') == 'FAILED':
                failed.append(receipt)
        elif name.startswith('manifest-'):
            ident = name[9:-5]
            receipt = directory.read('receipt-'+ident+'.json')
            if receipt is None:
                manifest = directory.read(name)
                pending.append({'snapshot_id': ident, 'manifest_sha256': p.digest(manifest), 'state': 'PENDING'})
        elif name.startswith('.capture-') or name.startswith('.write-'):
            # Existing writer lock proves the former writer is gone. No
            # committed manifest references either temporary naming class.
            directory.remove(name)
    if failed:
        if len(failed) != 1:
            raise ValueError('SHADOW_COLD_MULTIPLE_FAILED_BACKLOGS')
        first = failed[0]
        if not queue or queue.get('state') != 'FAILED':
            directory.write('queue.json', {'snapshot_id': first['snapshot_id'],
                'manifest_sha256': first['manifest_sha256'], 'state': 'FAILED'})
        raise ValueError('SHADOW_COLD_FAILED_BACKLOG_REQUIRES_FIX_FORWARD')
    # A crash before manifest fsync cannot have published a cut. Record that
    # fact before removing only the writer-owned uncommitted pack. Completed
    # packs left after receipt/queue fsync can also be reclaimed safely.
    names = {name for name, info in entries}
    for name, info in entries:
        if not name.endswith('.pack'):
            continue
        ident = name[:-5]
        if 'manifest-'+ident+'.json' not in names:
            with directory.open(name) as stream:
                checksum = _hash(stream)
            directory.write('receipt-'+ident+'.json', {'state': 'ABORTED_UNPUBLISHED',
                'snapshot_id': ident, 'pack_identity': _identity(info), 'pack_sha256': checksum})
            directory.remove(name)
        else:
            receipt = directory.read('receipt-'+ident+'.json')
            if receipt and receipt.get('state') == 'COMPLETE':
                manifest = directory.read('manifest-'+ident+'.json')
                resolve(files, {'schema': 'rc6.computed-audit-reference.v2', 'state': 'PENDING',
                    'snapshot_id': ident, 'manifest_sha256': p.digest(manifest), 'epoch': EPOCH})
                directory.remove(name)
    if len(pending) > 1:
        raise ValueError('SHADOW_COLD_BACKLOG_BOUND')
    if (pending and queue and queue.get('state') == 'COMPLETE' and
            queue.get('snapshot_id') == pending[0]['snapshot_id']):
        raise ValueError('SHADOW_COLD_COMPLETE_RECEIPT_MISSING')
    if pending and queue != pending[0]:
        if queue and queue.get('state') not in ('COMPLETE',):
            raise ValueError('SHADOW_COLD_QUEUE_CONFLICT')
        directory.write('queue.json', pending[0])
        queue = pending[0]
    if queue and queue.get('state') == 'FAILED':
        raise ValueError('SHADOW_COLD_FAILED_BACKLOG_REQUIRES_FIX_FORWARD')
    if queue and queue.get('state') == 'PENDING':
        raise ValueError('SHADOW_COLD_PENDING_BACKPRESSURE')
    if queue and queue.get('state') != 'COMPLETE':
        raise ValueError('SHADOW_COLD_QUEUE_STATE')
    if sum(info.st_size for name, info in directory.entries() if name.endswith(('.pack', '.tmp'))) > PACK_LIMIT:
        raise ValueError('SHADOW_COLD_PACK_QUOTA')


class _StoredValue(CanonicalJSONStream):
    def __init__(self, directory, manifest, check):
        self.directory, self.manifest, self.check = directory, manifest, check

    def iter_bytes(self):
        m = self.manifest
        # _drain verified manifest.pack_sha256 and its inode before this call.
        # Frame hashes and persisted report verification remain mandatory.
        yield from _expand(self.directory, m['pack'], m['root'], m['segments'],
                           check=self.check, pack_prevalidated=True)


def _drain(files, phase):
    """Materialize/replay one original snapshot, under the caller's writer lock."""
    phase[0] = 'NAMESPACE'
    with Directory(files, create=True) as directory:
        # Always reconcile manifest-before-queue crash gaps, including a new
        # seal beside the previous COMPLETE queue, before looking at state.
        phase[0] = 'BACKLOG_RECONCILE'
        try:
            ensure_idle(files, directory=directory)
        except ValueError as exc:
            if str(exc) != 'SHADOW_COLD_PENDING_BACKPRESSURE':
                raise
        queue = directory.read('queue.json')
        if queue is None:
            return None
        if queue.get('state') == 'FAILED':
            raise ValueError('SHADOW_COLD_FAILED_BACKLOG_REQUIRES_FIX_FORWARD')
        phase[0] = 'MANIFEST'
        manifest = _manifest(directory, queue)
        reference = {'schema': 'rc6.computed-audit-reference.v2', 'state': 'PENDING',
                     'snapshot_id': manifest['snapshot_id'], 'manifest_sha256': p.digest(manifest),
                     'epoch': EPOCH, 'source_as_of': manifest['source_as_of'],
                     'configuration_fingerprint': manifest['configuration_fingerprint'],
                     'deadline_at': manifest['deadline_at']}
        prior = directory.read('receipt-'+manifest['snapshot_id']+'.json')
        if prior is not None:
            # Crash after COMPLETE receipt fsync, before queue transition or
            # snapshot cleanup: retain the actual original completion time.
            phase[0] = 'PRIOR_RECEIPT'
            validated = resolve(files, reference)
            if queue.get('state') != 'COMPLETE':
                directory.write('queue.json', {**queue, 'state': 'COMPLETE'})
            try:
                directory.remove(manifest['pack'])
            except FileNotFoundError:
                pass
            return validated
        if queue.get('state') == 'COMPLETE':
            raise ValueError('SHADOW_COLD_COMPLETE_RECEIPT_MISSING')
        phase[0] = 'NATIVE'
        _native()
        if queue.get('state') != 'PENDING':
            raise ValueError('SHADOW_COLD_QUEUE_STATE')
        wall, cpu, monotonic_deadline = time.monotonic(), time.process_time(), time.monotonic()+DEADLINE
        checked_at = [0.0]
        def check():
            _check_deadline(manifest)
            if time.monotonic() > monotonic_deadline:
                raise ValueError('SHADOW_COLD_MONOTONIC_DEADLINE')
            if time.monotonic()-checked_at[0] >= .25:
                _guard(files.root)
                checked_at[0] = time.monotonic()
        phase[0] = 'INPUT_CHECK'
        check()
        _pack_identity(directory, manifest)
        phase[0] = 'PACK_HASH'
        with directory.open(manifest['pack']) as stream:
            if _hash(stream, guard=check) != manifest['pack_sha256']:
                raise ValueError('SHADOW_COLD_PACK_HASH')
        phase[0] = 'REPORT_WRITE'
        computed = files.write_streamed_report(_StoredValue(directory, manifest, check))
        phase[0] = 'REPORT_VERIFY'
        _pack_identity(directory, manifest)
        identity = _verified_report(files, computed, check)
        check()
        receipt = {'state': 'COMPLETE', 'epoch': EPOCH, 'snapshot_id': manifest['snapshot_id'],
                   'manifest_sha256': p.digest(manifest), 'source_as_of': manifest['source_as_of'],
                   'configuration_fingerprint': manifest['configuration_fingerprint'],
                   'computed_report': computed, 'computed_identity': identity,
                   'completed_at': time.time(), 'wall_seconds': time.monotonic()-wall,
                   'cpu_seconds': time.process_time()-cpu}
        phase[0] = 'RECEIPT_COMMIT'
        directory.write('receipt-'+manifest['snapshot_id']+'.json', receipt)
        phase[0] = 'QUEUE_COMMIT'
        directory.write('queue.json', {**queue, 'state': 'COMPLETE'})
        # Receipt+report are durable and independently verified. No reader
        # needs the snapshot once it resolves this COMPLETE attestation.
        phase[0] = 'PACK_CLEANUP'
        directory.remove(manifest['pack'])
        return receipt


def _failure_code(exc):
    """Persist only bounded internal error identifiers; never exception text."""
    args = exc.args
    code = args[0] if len(args) == 1 and type(args[0]) is str else None
    if isinstance(exc, ValueError) and code is not None and re.fullmatch(r'SHADOW_[A-Z0-9_]{1,120}', code):
        return code
    return 'UNCLASSIFIED'


def drain(files):
    """All failed transitions leave durable first-failure evidence, including
    invalid manifests, missing native APIs and COMPLETE receipt corruption.
    """
    phase = ['ENTRY']
    try:
        return _drain(files, phase)
    except Exception as exc:
        with Directory(files, create=True) as directory:
            queue = directory.read('queue.json')
            if (isinstance(queue, dict) and isinstance(queue.get('snapshot_id'), str)
                    and IDENT.fullmatch(queue['snapshot_id'])):
                name = 'receipt-'+queue['snapshot_id']+'.json'
                prior = directory.read(name)
                if not isinstance(prior, dict) or prior.get('state') != 'FAILED':
                    failure = {'state': 'FAILED', 'epoch': EPOCH,
                               'snapshot_id': queue['snapshot_id'],
                               'manifest_sha256': queue.get('manifest_sha256'),
                               'failed_at': time.time(), 'error_class': type(exc).__name__,
                               'error_code': _failure_code(exc), 'error_phase': phase[0]}
                    if prior is not None:
                        failure['previous_receipt'] = prior
                    directory.write(name, failure)
                if queue.get('state') != 'FAILED':
                    directory.write('queue.json', {**queue, 'state': 'FAILED'})
        raise


def resolve(files, reference):
    """Bounded reader validation; verified immutable file identities avoid a
    100MiB decompression on every dashboard request. Full bytes were read and
    hashed before COMPLETE; any later write/replace changes ctime/inode.
    """
    if reference.get('schema') != 'rc6.computed-audit-reference.v2' or reference.get('state') != 'PENDING':
        raise ValueError('SHADOW_COLD_REFERENCE_CONTRACT')
    with Directory(files) as directory:
        manifest = _manifest(directory, reference)
        receipt = directory.read('receipt-'+manifest['snapshot_id']+'.json')
        queue = directory.read('queue.json')
        if (queue and queue.get('snapshot_id') == manifest['snapshot_id'] and
                (queue.get('state') == 'FAILED' or
                 queue.get('state') == 'COMPLETE' and receipt is None)):
            raise ValueError('SHADOW_COLD_AUDIT_REVOKED')
        if receipt is not None:
            if (receipt.get('state') != 'COMPLETE' or receipt.get('epoch') != EPOCH or
                    receipt.get('snapshot_id') != manifest['snapshot_id'] or
                    receipt.get('manifest_sha256') != p.digest(manifest) or
                    receipt.get('source_as_of') != manifest['source_as_of'] or
                    receipt.get('configuration_fingerprint') != manifest['configuration_fingerprint'] or
                    not manifest['sealed_at'] <= receipt.get('completed_at', 0) <= manifest['deadline_at']):
                raise ValueError('SHADOW_COLD_AUDIT_REVOKED')
            stream, identity = _report_identity(files, receipt['computed_report'])
            with stream:
                if identity != receipt.get('computed_identity'):
                    raise ValueError('SHADOW_COLD_COMPUTED_CHANGED_AFTER_VERIFICATION')
            return receipt
        _check_deadline(manifest)
        _pack_identity(directory, manifest)
        return {'state': 'PENDING', 'snapshot_id': manifest['snapshot_id'],
                'manifest_sha256': p.digest(manifest), 'deadline_at': manifest['deadline_at']}


def prune_completed(files):
    """Retain both immutable cuts and their full audits; never prune failures."""
    with Directory(files, create=True) as directory:
        active, reports = set(), set()
        for path in files.root.glob('cut-*.json.gz'):
            if not re.fullmatch(r'cut-[a-f0-9]{64}\.json\.gz', path.name):
                continue
            cut = files.read(path.name)
            reference = (cut or {}).get('report', {}).get('computed_report', {})
            if reference.get('schema') == 'rc6.computed-audit-reference.v2':
                active.add(reference.get('snapshot_id'))
                receipt = directory.read('receipt-'+reference['snapshot_id']+'.json')
                if receipt and receipt.get('state') == 'COMPLETE':
                    reports.add(receipt['computed_report']['file'])
            elif reference.get('file'):
                reports.add(reference['file'])
        queue = directory.read('queue.json')
        if queue:
            active.add(queue.get('snapshot_id'))
        for name, info in directory.entries():
            if not name.startswith('receipt-'):
                continue
            ident = name[8:-5]
            receipt = directory.read(name)
            if ident in active or receipt.get('state') != 'COMPLETE':
                continue
            report = receipt['computed_report']['file']
            if report not in reports:
                target = files.path(report)
                if target.exists():
                    target.unlink()
            directory.remove('manifest-'+ident+'.json')
            directory.remove(name)
        rootfd = os.open(files.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(rootfd)
        finally:
            os.close(rootfd)
