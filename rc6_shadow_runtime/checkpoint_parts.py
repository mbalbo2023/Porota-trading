"""One atomic standard gzip checkpoint, with verified lazy deflate ranges.

The decompressed envelope is unchanged. No persistent sidecar or role cache
exists. Gzip's bounded extra header records full-flush boundaries, and both
the complete envelope and the recomposed role values are verified.
"""
import gzip
import hashlib
import json
import os
import re
import struct
import sys
import tempfile
import zlib

from .audit_spool import BLOCK, CanonicalJSONStream

ROLES = frozenset(('engines', 'lab', 'entry_signals', 'funnel'))
EXTRA_BYTES = 4096
HEADER_BYTES = 10+2+EXTRA_BYTES
PREFIX = b'{"payload":'
TAIL_PREFIX = b',"digest":"'
TAIL_BYTES = len(TAIL_PREFIX)+64+2


def funnel_metadata_decoder():
    """Share only equal immutable metadata in causal funnel rows, per read."""
    pool, charged = {}, 0
    names = ('session', 'strategy_id', 'strategy_version', 'configuration_fingerprint',
             'registry_sha256', 'family', 'currency', 'channel', 'market', 'settlement')
    def share(value):
        nonlocal charged
        if type(value) is not str or len(value) > 1024:
            return value
        prior = pool.get(value)
        if prior is not None:
            return prior
        cost = sys.getsizeof(value)+128
        if len(pool) < 1024 and charged+cost <= 128*1024:
            pool[value] = value
            charged += cost
        return value
    def decode(value):
        if 'session' in value and 'strategy_id' in value:
            for name in names:
                if name in value:
                    value[name] = share(value[name])
            identity = value.get('identity')
            if type(identity) is list and len(identity) == 5:
                for index in range(1, 5):
                    identity[index] = share(identity[index])
        elif type(value.get('CATALOG_READY')) is str:
            for key, item in value.items():
                value[key] = share(item)
        return value
    return decode


def _write_all(stream, data):
    pending = memoryview(data)
    while pending:
        written = stream.write(pending)
        if not written:
            raise OSError('SHADOW_CHECKPOINT_SHORT_WRITE')
        pending = pending[written:]


def write_indexed(stream, payload, encode, payload_limit):
    if type(payload) is not dict or any(type(key) is not str for key in payload):
        raise ValueError('SHADOW_CHECKPOINT_OBJECT_REQUIRED')
    # Valid single-member gzip, including a standards-compliant RC extra field.
    header = (b'\x1f\x8b\x08\x04'+struct.pack('<I', 0)+b'\x04\xff'
              +struct.pack('<H', EXTRA_BYTES)+b'RC'+struct.pack('<H', EXTRA_BYTES-4))
    _write_all(stream, header+b'\0'*(EXTRA_BYTES-4))
    compressor = zlib.compressobj(1, zlib.DEFLATED, -15)
    crc = total = payload_bytes = 0
    digest = hashlib.sha256()
    ranges = {}
    def emit(raw, body=True):
        nonlocal crc, total, payload_bytes
        if type(raw) is not bytes:
            raise TypeError('SHADOW_CHECKPOINT_BYTES_REQUIRED')
        if body:
            payload_bytes += len(raw)
            if payload_bytes > payload_limit:
                raise ValueError('SHADOW_CHECKPOINT_PAYLOAD_LIMIT')
            digest.update(raw)
        crc = zlib.crc32(raw, crc)
        total += len(raw)
        _write_all(stream, compressor.compress(raw))
    emit(PREFIX, False)
    emit(b'{')
    for index, key in enumerate(sorted(payload)):
        if index:
            emit(b',')
        emit(json.dumps(key, ensure_ascii=True).encode('ascii')+b':')
        if key in ROLES:
            _write_all(stream, compressor.flush(zlib.Z_FULL_FLUSH))
            start, raw_start, count = stream.tell(), total, 0
            role_digest = hashlib.sha256()
            for chunk in encode(payload[key]):
                emit(chunk)
                role_digest.update(chunk)
                count += len(chunk)
            _write_all(stream, compressor.flush(zlib.Z_FULL_FLUSH))
            ranges[key] = {'compressed_start': start, 'compressed_bytes': stream.tell()-start,
                           'raw_start': raw_start, 'raw_bytes': count,
                           'sha256': role_digest.hexdigest()}
        else:
            for chunk in encode(payload[key]):
                emit(chunk)
    emit(b'}')
    emit(TAIL_PREFIX+digest.hexdigest().encode('ascii')+b'"}', False)
    _write_all(stream, compressor.flush(zlib.Z_FINISH))
    _write_all(stream, struct.pack('<II', crc & 0xffffffff, total & 0xffffffff))
    end = stream.tell()
    layout = {'schema': 'rc6.checkpoint-ranges.v1', 'roles': ranges,
              'payload_sha256': digest.hexdigest(), 'raw_envelope_bytes': total,
              'payload_bytes': payload_bytes}
    raw_index = json.dumps(layout, sort_keys=True, separators=(',', ':')).encode('ascii')
    if len(raw_index) > EXTRA_BYTES-4:
        raise ValueError('SHADOW_CHECKPOINT_INDEX_LIMIT')
    stream.seek(len(header))
    _write_all(stream, raw_index+b'\0'*(EXTRA_BYTES-4-len(raw_index)))
    stream.seek(end)
    return layout


def _index(stream):
    stream.seek(0)
    raw = stream.read(HEADER_BYTES)
    if (len(raw) < HEADER_BYTES or raw[:4] != b'\x1f\x8b\x08\x04'
            or raw[10:16] != struct.pack('<H', EXTRA_BYTES)+b'RC'+struct.pack('<H', EXTRA_BYTES-4)):
        return None
    try:
        value = json.loads(raw[16:].rstrip(b'\0'))
    except (ValueError, UnicodeError) as exc:
        raise ValueError('SHADOW_CHECKPOINT_INDEX_INVALID') from exc
    if value.get('schema') != 'rc6.checkpoint-ranges.v1' or not isinstance(value.get('roles'), dict):
        raise ValueError('SHADOW_CHECKPOINT_INDEX_INVALID')
    return value


class _EnvelopeDigest:
    def __init__(self, limit):
        self.prefix, self.tail = b'', b''
        self.digest, self.total, self.limit = hashlib.sha256(), 0, limit

    def add(self, chunk):
        self.total += len(chunk)
        if self.total > self.limit+len(PREFIX)+TAIL_BYTES:
            raise ValueError('SHADOW_CHECKPOINT_PAYLOAD_LIMIT')
        if len(self.prefix) < len(PREFIX):
            needed = len(PREFIX)-len(self.prefix)
            self.prefix += chunk[:needed]
            chunk = chunk[needed:]
        combined = self.tail+chunk
        if len(combined) > TAIL_BYTES:
            self.digest.update(combined[:-TAIL_BYTES])
            self.tail = combined[-TAIL_BYTES:]
        else:
            self.tail = combined

    def finish(self):
        if (self.prefix != PREFIX or len(self.tail) != TAIL_BYTES
                or not self.tail.startswith(TAIL_PREFIX) or not self.tail.endswith(b'"}')):
            raise ValueError('SHADOW_CHECKPOINT_ENVELOPE_INVALID')
        expected = self.tail[len(TAIL_PREFIX):-2].decode('ascii')
        if self.digest.hexdigest() != expected:
            raise ValueError('SHADOW_EVIDENCE_DIGEST_MISMATCH')
        return expected


def _file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(BLOCK):
            digest.update(chunk)
    return digest.hexdigest()


class CheckpointParts:
    def __init__(self, files, encode):
        self.files, self.encode = files, encode
        self.path = files.path('checkpoint.json.gz')
        self.stream = None
        self.head, self.roles = {}, {}
        self.source_sha = None
        self.identity = None
        self.layout = None

    def __enter__(self):
        if not self.path.exists():
            return self
        if self.path.stat().st_size > self.files.payload_limit:
            raise ValueError('SHADOW_CHECKPOINT_COMPRESSED_LIMIT')
        self.source_sha = _file_hash(self.path)
        self.stream = self.path.open('rb')
        try:
            layout = _index(self.stream)
            if layout is None:
                # One legacy bootstrap, never a graph retained beside planners.
                payload = self.files.read('checkpoint.json.gz', compact_checkpoint=True)
                if type(payload) is not dict:
                    raise ValueError('SHADOW_CHECKPOINT_OBJECT_REQUIRED')
                self.stream.close()
                self.stream = tempfile.TemporaryFile(mode='w+b', dir=self.files.root)
                layout = write_indexed(self.stream, payload, self.encode, self.files.payload_limit)
                self.stream.flush()
                del payload
            self.layout = layout
            stat = os.fstat(self.stream.fileno())
            self.identity = (stat.st_dev, stat.st_ino, stat.st_size)
            self._validate()
            return self
        except BaseException:
            self.__exit__()
            raise

    def _validate(self):
        limit, layout = self.files.payload_limit, self.layout
        ranges = []
        for role, info in layout['roles'].items():
            if role not in ROLES or not isinstance(info, dict):
                raise ValueError('SHADOW_CHECKPOINT_ROLE_INVALID')
            for key in ('compressed_start', 'compressed_bytes', 'raw_start', 'raw_bytes'):
                if type(info.get(key)) is not int or info[key] < 0:
                    raise ValueError('SHADOW_CHECKPOINT_RANGE_INVALID')
            if (not re.fullmatch('[0-9a-f]{64}', str(info.get('sha256')))
                    or not 0 < info['raw_bytes'] <= limit
                    or info['compressed_start'] < HEADER_BYTES
                    or info['compressed_start']+info['compressed_bytes'] > self.identity[2]-8
                    or info['raw_start'] < len(PREFIX)
                    or info['raw_start']+info['raw_bytes'] > limit+len(PREFIX)):
                raise ValueError('SHADOW_CHECKPOINT_RANGE_INVALID')
            ranges.append((info['raw_start'], info['raw_start']+info['raw_bytes'], role))
        ranges.sort()
        if any(b[0] < a[1] for a, b in zip(ranges, ranges[1:])):
            raise ValueError('SHADOW_CHECKPOINT_RANGE_OVERLAP')
        envelope = _EnvelopeDigest(limit)
        metadata, position, index = bytearray(), 0, 0
        role_hashes = {role: hashlib.sha256() for role in layout['roles']}
        self.stream.seek(0)
        with gzip.GzipFile(fileobj=self.stream, mode='rb') as zipped:
            while chunk := zipped.read(BLOCK):
                envelope.add(chunk)
                at = 0
                while at < len(chunk):
                    if index >= len(ranges):
                        metadata.extend(chunk[at:])
                        break
                    start, end, role = ranges[index]
                    current = position+at
                    if current < start:
                        count = min(start-current, len(chunk)-at)
                        metadata.extend(chunk[at:at+count])
                    else:
                        if current == start:
                            metadata.extend(b'null')
                        count = min(end-current, len(chunk)-at)
                        if count <= 0:
                            raise ValueError('SHADOW_CHECKPOINT_RANGE_INVALID')
                        role_hashes[role].update(chunk[at:at+count])
                        if current+count == end:
                            index += 1
                    at += count
                position += len(chunk)
        digest = envelope.finish()
        if (index != len(ranges) or position != layout.get('raw_envelope_bytes')
                or digest != layout.get('payload_sha256')):
            raise ValueError('SHADOW_CHECKPOINT_INDEX_MISMATCH')
        for role, hasher in role_hashes.items():
            if hasher.hexdigest() != layout['roles'][role]['sha256']:
                raise ValueError('SHADOW_CHECKPOINT_ROLE_DIGEST_MISMATCH')
        decoded = json.loads(metadata)
        if not isinstance(decoded, dict) or not isinstance(decoded.get('payload'), dict):
            raise ValueError('SHADOW_CHECKPOINT_OBJECT_REQUIRED')
        self.head = {key: value for key, value in decoded['payload'].items() if key not in layout['roles']}
        self.roles = {role: DeflateRole(self, info) for role, info in layout['roles'].items()}
        composition = {**self.head, **self.roles}
        actual = hashlib.sha256()
        for chunk in self.encode(composition):
            actual.update(chunk)
        if actual.hexdigest() != digest:
            raise ValueError('SHADOW_CHECKPOINT_ROLE_COMPOSITION_MISMATCH')

    def _fd(self):
        if self.stream is None or self.stream.closed:
            raise ValueError('SHADOW_CHECKPOINT_PARTS_CLOSED')
        fd = self.stream.fileno()
        stat = os.fstat(fd)
        if (stat.st_dev, stat.st_ino, stat.st_size) != self.identity:
            raise ValueError('SHADOW_CHECKPOINT_IDENTITY_CHANGED')
        return fd

    def load(self, role):
        if role not in ROLES:
            raise ValueError('SHADOW_CHECKPOINT_ROLE_INVALID')
        if self.source_sha is not None and _file_hash(self.path) != self.source_sha:
            raise ValueError('SHADOW_CHECKPOINT_CHANGED_DURING_TICK')
        node = self.roles.get(role)
        if node is None:
            return None
        raw = bytearray()
        for chunk in node.iter_bytes():
            raw.extend(chunk)
        text = raw.decode('ascii')
        del raw
        value = json.loads(text, object_hook=funnel_metadata_decoder() if role == 'funnel' else None)
        del text
        return value

    def __exit__(self, *args):
        if self.stream is not None:
            self.stream.close()
            self.stream = None


class DeflateRole(CanonicalJSONStream):
    __slots__ = ('owner', 'info')

    def __init__(self, owner, info):
        self.owner, self.info = owner, info

    def iter_bytes(self):
        fd, info = self.owner._fd(), self.info
        decoder = zlib.decompressobj(-15)
        digest, written, read = hashlib.sha256(), 0, 0
        while read < info['compressed_bytes']:
            part = os.pread(fd, min(BLOCK, info['compressed_bytes']-read), info['compressed_start']+read)
            if not part:
                raise ValueError('SHADOW_CHECKPOINT_ROLE_TRUNCATED')
            read += len(part)
            while part:
                raw = decoder.decompress(part, BLOCK)
                part = decoder.unconsumed_tail
                if raw:
                    written += len(raw)
                    if written > info['raw_bytes']:
                        raise ValueError('SHADOW_CHECKPOINT_ROLE_SIZE_MISMATCH')
                    digest.update(raw)
                    yield raw
        while raw := decoder.decompress(b'', BLOCK):
            written += len(raw)
            if written > info['raw_bytes']:
                raise ValueError('SHADOW_CHECKPOINT_ROLE_SIZE_MISMATCH')
            digest.update(raw)
            yield raw
        if (written != info['raw_bytes'] or digest.hexdigest() != info['sha256']
                or decoder.unused_data):
            raise ValueError('SHADOW_CHECKPOINT_ROLE_DIGEST_MISMATCH')
