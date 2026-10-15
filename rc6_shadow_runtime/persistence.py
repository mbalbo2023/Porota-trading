"""Exclusive bounded private files. Trading inputs are never writable outputs."""
import fcntl
import gzip
import hashlib
import json
import os
import re
from pathlib import Path
import tempfile

from rc6_dynamic_universe.common import digest


class EvidencePayloadLimit(ValueError):
    """Bounded context for a rejected evidence payload; never contains its data."""
    def __init__(self, name, measured_bytes, limit_bytes):
        super().__init__("SHADOW_PAYLOAD_LIMIT")
        self.evidence_file = (name if name in {"latest.json.gz", "checkpoint.json.gz", "status.json"}
                              else "preopen" if name.startswith("preopen-") and name.endswith(".json.gz")
                              else "other")
        self.measured_bytes = int(measured_bytes)
        self.limit_bytes = int(limit_bytes)


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
            raise EvidencePayloadLimit(name, path.stat().st_size, self.payload_limit)
        opener = gzip.open if name.endswith(".gz") else open
        with opener(path, "rb") as stream:
            raw = stream.read(self.payload_limit + 1)
        if len(raw) > self.payload_limit:
            raise EvidencePayloadLimit(name, len(raw), self.payload_limit)
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
            raise EvidencePayloadLimit(name, len(encoded), self.payload_limit)
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

    def write_streamed_report(self, payload, *, raw_limit=512 * 1024**2):
        """Store canonical full audit separately from the bounded hot-path view."""
        if self.lock is None:
            raise ValueError("SHADOW_WRITER_LOCK_REQUIRED")
        encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"),
                                   default=str, allow_nan=False)
        hasher = hashlib.sha256()
        raw_bytes = 0
        with tempfile.NamedTemporaryFile(dir=self.root, prefix=".computed-report.",
                suffix=".tmp", delete=False) as stream:
            tmp = Path(stream.name)
            try:
                with gzip.GzipFile(fileobj=stream, mode="wb", compresslevel=1, mtime=0) as zipped:
                    for fragment in encoder.iterencode(payload):
                        chunk = fragment.encode()
                        raw_bytes += len(chunk)
                        if raw_bytes > raw_limit:
                            raise EvidencePayloadLimit("computed-report", raw_bytes, raw_limit)
                        hasher.update(chunk)
                        zipped.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
                name = "computed-" + hasher.hexdigest() + ".json.gz"
                target = self.path(name)
                files = list(self.root.iterdir())
                if len(files) > 512 or sum(p.lstat().st_size for p in files) > self.maximum_bytes:
                    raise ValueError("SHADOW_EVIDENCE_CAPACITY_REACHED")
                if not target.exists():
                    tmp.replace(target)
                    directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                return {"file": name, "sha256": hasher.hexdigest(),
                        "raw_bytes": raw_bytes, "compressed_bytes": target.stat().st_size}
            finally:
                tmp.unlink(missing_ok=True)

    def prune_computed_reports(self, *, keep):
        """After the new view and status commit, remove superseded private audits."""
        if self.lock is None or not re.fullmatch(r"computed-[a-f0-9]{64}\.json\.gz", keep):
            raise ValueError("SHADOW_AUDIT_PRUNE_INVALID")
        for target in self.root.glob("computed-*.json.gz"):
            if target.name != keep and re.fullmatch(r"computed-[a-f0-9]{64}\.json\.gz", target.name):
                self.path(target.name).unlink()
