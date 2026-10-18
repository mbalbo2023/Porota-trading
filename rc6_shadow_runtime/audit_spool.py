"""Bounded anonymous staging of actual canonical audit bytes, never readiness."""
import hashlib
import os
import re
import shutil
import tempfile
import time

BLOCK = 65536
RESERVE = 2 * 1024**3
MAXIMUM_BYTES = (512 + 64) * 1024**2
DIRTY_BATCH = 8 * 1024**2


class CanonicalJSONStream:
    """Internal ASCII canonical value whose iterator verifies actual bytes."""
    def iter_bytes(self):
        raise NotImplementedError

    def iter_ascii(self):
        for chunk in self.iter_bytes():
            try:
                yield chunk.decode('ascii')
            except UnicodeDecodeError as exc:
                raise ValueError('SHADOW_CANONICAL_STREAM_NOT_ASCII') from exc


class AuditSpool:
    def __init__(self, files, maximum_bytes=MAXIMUM_BYTES):
        if (type(maximum_bytes) is not int or not 0 < maximum_bytes <= MAXIMUM_BYTES
                or files.lock is None):
            raise ValueError('SHADOW_SPOOL_OWNER_OR_BUDGET_INVALID')
        self.files, self.maximum_bytes = files, maximum_bytes
        self.total_bytes, self.last_sync = 0, 0
        self.stream, self.identity = None, None
        self.profile = []

    def __enter__(self):
        self.files.lock.fileno()
        if shutil.disk_usage(self.files.root).free < RESERVE + self.maximum_bytes:
            raise ValueError('SHADOW_SPOOL_DISK_ADMISSION_BLOCKED')
        self.stream = tempfile.TemporaryFile(mode='w+b', buffering=0, dir=self.files.root)
        stat = os.fstat(self.stream.fileno())
        if stat.st_nlink != 0:
            self.stream.close()
            self.stream = None
            raise ValueError('SHADOW_SPOOL_MUST_BE_ANONYMOUS')
        self.identity = (stat.st_dev, stat.st_ino)
        return self

    def _fd(self):
        if self.stream is None or self.stream.closed:
            raise ValueError('SHADOW_SPOOL_CLOSED')
        fd = self.stream.fileno()
        stat = os.fstat(fd)
        if (stat.st_dev, stat.st_ino) != self.identity or stat.st_nlink != 0:
            raise ValueError('SHADOW_SPOOL_IDENTITY_CHANGED')
        return fd

    def _sync(self):
        fd = self._fd()
        self.stream.flush()
        os.fsync(fd)
        if hasattr(os, 'posix_fadvise'):
            os.posix_fadvise(fd, self.last_sync, self.total_bytes-self.last_sync,
                             os.POSIX_FADV_DONTNEED)
        self.last_sync = self.total_bytes

    def stage(self, role, chunks):
        if not re.fullmatch('[A-Za-z0-9_]{1,64}', role) or len(self.profile) >= 64:
            raise ValueError('SHADOW_SPOOL_ROLE_BUDGET')
        self._fd()
        started, cpu = time.monotonic(), time.process_time()
        offset, count = self.total_bytes, 0
        hasher = hashlib.sha256()
        for chunk in chunks:
            if type(chunk) is not bytes:
                raise TypeError('SHADOW_SPOOL_BYTES_REQUIRED')
            for at in range(0, len(chunk), BLOCK):
                part = chunk[at:at+BLOCK]
                if self.total_bytes+len(part) > self.maximum_bytes:
                    raise ValueError('SHADOW_SPOOL_CAPACITY_REACHED')
                if self.total_bytes-self.last_sync+len(part) > DIRTY_BATCH:
                    self._sync()
                if shutil.disk_usage(self.files.root).free < RESERVE+len(part):
                    raise ValueError('SHADOW_SPOOL_DISK_RESERVE_BREACHED')
                pending = memoryview(part)
                while pending:
                    written = self.stream.write(pending)
                    if not written:
                        raise OSError('SHADOW_SPOOL_SHORT_WRITE')
                    pending = pending[written:]
                self.total_bytes += len(part)
                count += len(part)
                hasher.update(part)
        self._sync()
        result = SpoolValue(self, offset, count, hasher.hexdigest())
        self.profile.append({'role': role, 'raw_bytes': count,
                             'wall_seconds': round(time.monotonic()-started, 6),
                             'cpu_seconds': round(time.process_time()-cpu, 6)})
        return result

    def __exit__(self, *args):
        if self.stream is not None:
            self.stream.close()
            self.stream = None


class SpoolValue(CanonicalJSONStream):
    __slots__ = ('owner', 'offset', 'raw_bytes', 'sha256')

    def __init__(self, owner, offset, raw_bytes, sha256):
        self.owner, self.offset = owner, offset
        self.raw_bytes, self.sha256 = raw_bytes, sha256

    def iter_bytes(self):
        fd = self.owner._fd()
        if self.offset < 0 or self.offset+self.raw_bytes > self.owner.total_bytes:
            raise ValueError('SHADOW_SPOOL_RANGE_INVALID')
        hasher, count = hashlib.sha256(), 0
        while count < self.raw_bytes:
            chunk = os.pread(fd, min(BLOCK, self.raw_bytes-count), self.offset+count)
            if not chunk:
                raise ValueError('SHADOW_SPOOL_TRUNCATED')
            hasher.update(chunk)
            count += len(chunk)
            yield chunk
        if count != self.raw_bytes or hasher.hexdigest() != self.sha256:
            raise ValueError('SHADOW_SPOOL_DIGEST_MISMATCH')
