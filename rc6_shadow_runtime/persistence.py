"""Exclusive bounded private files. Trading inputs are never writable outputs."""
import fcntl
import gzip
import json
import os
from pathlib import Path
import tempfile

from rc6_dynamic_universe.common import digest


class EvidenceFiles:
    def __init__(self, root, *, protected=(), maximum_bytes=128 * 1024**2,
                 payload_limit=64 * 1024**2):
        raw = Path(root).absolute()
        if raw.is_symlink():
            raise ValueError("SHADOW_DIRECTORY_ALIAS_FORBIDDEN")
        self.root = raw.resolve()
        self.protected = {Path(p).resolve() for p in protected if p is not None}
        if any(p == self.root or p.is_relative_to(self.root) for p in self.protected):
            raise ValueError("SHADOW_OUTPUT_MUST_BE_SEPARATE")
        if maximum_bytes <= 0 or payload_limit <= 0:
            raise ValueError("INVALID_EVIDENCE_BUDGET")
        self.maximum_bytes, self.payload_limit = maximum_bytes, payload_limit
        self.lock = None

    def __enter__(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.path("writer.lock")
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        self.lock = os.fdopen(fd, "a+")
        try:
            if os.fstat(fd).st_nlink != 1:
                raise ValueError("SHADOW_FILE_ALIAS_FORBIDDEN")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.lock.close()
            self.lock = None
            raise
        return self

    def __exit__(self, *args):
        if self.lock is not None:
            self.lock.close()
            self.lock = None

    def path(self, name):
        if Path(name).name != name:
            raise ValueError("SHADOW_BASENAME_REQUIRED")
        target = self.root / name
        if target.is_symlink() or (target.exists() and target.stat().st_nlink != 1):
            raise ValueError("SHADOW_FILE_ALIAS_FORBIDDEN")
        if target.resolve() in self.protected:
            raise ValueError("SHADOW_OUTPUT_MUST_BE_SEPARATE")
        return target

    def read(self, name):
        path = self.path(name)
        if not path.exists():
            return None
        if path.stat().st_size > self.payload_limit:
            raise ValueError("SHADOW_PAYLOAD_LIMIT")
        opener = gzip.open if name.endswith(".gz") else open
        with opener(path, "rb") as stream:
            raw = stream.read(self.payload_limit + 1)
        if len(raw) > self.payload_limit:
            raise ValueError("SHADOW_PAYLOAD_LIMIT")
        envelope = json.loads(raw)
        if envelope.get("digest") != digest(envelope.get("payload")):
            raise ValueError("SHADOW_EVIDENCE_DIGEST_MISMATCH")
        return envelope["payload"]

    def write(self, name, payload, *, immutable=False):
        if self.lock is None:
            raise ValueError("SHADOW_WRITER_LOCK_REQUIRED")
        target = self.path(name)
        if immutable and target.exists():
            existing = self.read(name)
            if digest(existing) != digest(payload):
                raise ValueError("SHADOW_IMMUTABLE_FREEZE_CONFLICT")
            return
        encoded = json.dumps({"digest": digest(payload), "payload": payload},
            sort_keys=True, separators=(",", ":"), default=str, allow_nan=False).encode()
        if len(encoded) > self.payload_limit:
            raise ValueError("SHADOW_PAYLOAD_LIMIT")
        data = gzip.compress(encoded, mtime=0) if name.endswith(".gz") else encoded
        files = list(self.root.iterdir())
        # The fsynced staging file temporarily coexists with the prior target.
        # Both file and byte quotas bound that peak, not just the final rename.
        if len(files) + 1 > 512:
            raise ValueError("SHADOW_EVIDENCE_CAPACITY_REACHED")
        size = sum(p.lstat().st_size for p in files)
        if size + len(data) > self.maximum_bytes:
            raise ValueError("SHADOW_EVIDENCE_CAPACITY_REACHED")
        with tempfile.NamedTemporaryFile(dir=self.root, prefix="." + name + ".",
                suffix=".tmp", delete=False) as stream:
            tmp = Path(stream.name)
            try:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
                # No overwrite of existing sources via a deterministic .tmp alias.
                tmp.replace(target)
                directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                tmp.unlink(missing_ok=True)
