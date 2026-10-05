"""Quota admission and acknowledged rotation for private SHADOW evidence.

Call prepare only while EvidenceFiles owns its exclusive writer flock. Readers
hold a shared flock, so generation rotation cannot race a committed read. No
trading source, provider, broker, container, or external archive is accessed.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import errno
import hashlib
import gzip
import json
import math
import os
from pathlib import Path
import re
import stat
import tarfile
import uuid

from rc6_dynamic_universe.common import digest, stamp


GENERATION = re.compile(r"gen-([0-9a-f]{32})\Z")
STAGING = re.compile(r"\.generation-([0-9a-f]{32})\.tmp\Z")
DELETING = re.compile(r"\.deleting-([0-9a-f]{32})\Z")
OWN_TEMP = re.compile(r"\.(?:CURRENT\.json|latest\.json\.gz|checkpoint\.json\.gz|status\.json|"
    r"preopen-\d{4}-\d{2}-\d{2}\.json\.gz)\.[a-z0-9_]{8}\.tmp\Z|"
    r"\.CURRENT\.[0-9a-f]{32}\.tmp\Z|\.independent-[0-9a-f]{32}\.tmp\Z|\.control-[0-9a-f]{32}\.tmp\Z")
BASE_MEMBERS = frozenset({"report.json.gz", "checkpoint.json.gz", "status.json", "manifest.json"})
MEMBERS = BASE_MEMBERS | {"projection.sqlite"}
HEX = re.compile(r"[0-9a-f]{64}\Z")
CONTROL_LIMIT = 256 * 1024
ARCHIVE_SCHEMA = "RC6_SHADOW_ARCHIVE_ACK_V2"
ARCHIVER_ID = "RC6_LOCAL_PRIVATE_ARCHIVER_V2"
CHECKPOINT_SCHEMA = "RC6_SHADOW_ARCHIVE_CHECKPOINT_V2"
CONTRACTED_HORIZON_SECONDS = 9 * 60 * 60


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("RETENTION_CONTROL_DUPLICATE_KEY")
        value[key] = item
    return value


class RetentionPressure(ValueError):
    """An explicit SHADOW-only admission denial, carrying alert/status evidence."""

    def __init__(self, reason, metrics):
        self.reason = reason
        self.metrics = {**metrics, "status": "RETENTION_PRESSURE", "reason": reason,
            "shadow_degraded": True, "factual_paths_effect": "NONE",
            "real_orders_sent": 0, "real_routes": "NOT_CALLED"}
        super().__init__(reason)


@dataclass(frozen=True)
class RetentionPolicy:
    maximum_bytes: int = 128 * 1024**2
    maximum_files: int = 512
    soft_ratio: float = .80
    control_reserve_bytes: int = 64 * 1024

    def __post_init__(self):
        for value in (self.maximum_bytes, self.maximum_files, self.control_reserve_bytes):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError("INVALID_RETENTION_POLICY")
        if (isinstance(self.soft_ratio, bool) or not math.isfinite(self.soft_ratio) or
                not 0 < self.soft_ratio < 1):
            raise ValueError("INVALID_RETENTION_POLICY")


def _generation_id(value):
    raw = str(value)
    matched = GENERATION.fullmatch(raw)
    raw = matched[1] if matched else raw
    if not re.fullmatch(r"[0-9a-f]{32}", raw):
        raise ValueError("RETENTION_GENERATION_ID_INVALID")
    return raw


class EvidenceRetention:
    def __init__(self, root, *, maximum_bytes=128 * 1024**2, maximum_files=512,
                 soft_ratio=.80, control_reserve_bytes=64 * 1024, archive_root=None,
                 archive_maximum_bytes=512 * 1024**2, archive_maximum_files=32768,
                 auto_archive=False, fault_inject=None):
        self.policy = RetentionPolicy(maximum_bytes, maximum_files, soft_ratio, control_reserve_bytes)
        self.root = Path(root).absolute()
        # Resolving first would hide an alias in the root or an ancestor.
        if any(path.is_symlink() for path in (self.root, *self.root.parents)):
            raise ValueError("RETENTION_DIRECTORY_ALIAS_FORBIDDEN")
        if not self.root.is_dir():
            raise ValueError("RETENTION_DIRECTORY_REQUIRED")
        self.root = self.root.resolve()
        self.archive_root = Path(archive_root).absolute() if archive_root is not None else None
        if self.archive_root is not None:
            if any(path.is_symlink() for path in (self.archive_root, *self.archive_root.parents)):
                raise ValueError("RETENTION_DIRECTORY_ALIAS_FORBIDDEN")
            self.archive_root = self.archive_root.resolve()
            if self.archive_root == self.root or self.archive_root.is_relative_to(self.root) or self.root.is_relative_to(self.archive_root):
                raise ValueError("RETENTION_ARCHIVE_MUST_BE_SEPARATE")
        if any(type(value) is not int or value <= 0 for value in (archive_maximum_bytes, archive_maximum_files)):
            raise ValueError("INVALID_RETENTION_ARCHIVE_POLICY")
        self.archive_maximum_bytes, self.archive_maximum_files = archive_maximum_bytes, archive_maximum_files
        self.auto_archive, self.fault_inject = auto_archive, fault_inject

    def _fault(self, stage):
        if self.fault_inject is not None:
            self.fault_inject(stage)

    def _durable_control(self, path, value):
        if path.is_symlink() or (path.exists() and path.stat().st_nlink != 1):
            raise ValueError("RETENTION_FILE_ALIAS_FORBIDDEN")
        data = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if len(data) > CONTROL_LIMIT:
            raise ValueError("RETENTION_CONTROL_LIMIT")
        temporary = path.parent / (".control-" + uuid.uuid4().hex + ".tmp")
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            temporary.replace(path)
            self._sync_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)

    def _archive_inventory(self, *, additional_bytes=0, additional_files=0):
        if self.archive_root is None:
            raise ValueError("RETENTION_ARCHIVE_DESTINATION_UNCONFIGURED")
        self.archive_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if any(path.is_symlink() for path in (self.archive_root, *self.archive_root.parents)):
            raise ValueError("RETENTION_DIRECTORY_ALIAS_FORBIDDEN")
        count, size = 0, 0
        for path in self.archive_root.iterdir():
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("RETENTION_ARCHIVE_MEMBER_INVALID")
            if re.fullmatch(r"\.(?:archive|control)-[0-9a-f]{32}\.tmp", path.name):
                path.unlink()
                self._sync_directory(self.archive_root)
                continue
            count += 1; size += info.st_size
            if count > self.archive_maximum_files:
                raise RetentionPressure("RETENTION_ARCHIVE_FILES_CAPACITY_REACHED", {"archive_files": count})
        if count + additional_files > self.archive_maximum_files or size + additional_bytes > self.archive_maximum_bytes:
            raise RetentionPressure("RETENTION_ARCHIVE_CAPACITY_REACHED", {
                "archive_files": count, "archive_bytes": size,
                "archive_projected_files": count + additional_files, "archive_projected_bytes": size + additional_bytes,
                "archive_maximum_files": self.archive_maximum_files, "archive_maximum_bytes": self.archive_maximum_bytes})
        available = os.statvfs(self.archive_root)
        if available.f_bavail * available.f_frsize < additional_bytes + self.policy.control_reserve_bytes:
            raise RetentionPressure("RETENTION_ARCHIVE_NO_SPACE", {"archive_bytes": size})
        return count, size

    def _lock_archive(self):
        if self.archive_root is None:
            return None
        missing = []
        for path in (self.archive_root, *self.archive_root.parents):
            if path.exists():
                break
            missing.append(path)
        self.archive_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if any(path.is_symlink() for path in (self.archive_root, *self.archive_root.parents)):
            raise ValueError("RETENTION_DIRECTORY_ALIAS_FORBIDDEN")
        for path in reversed(missing):
            self._sync_directory(path.parent)
        path = self.archive_root / "archive.lock"
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info, target = os.fstat(fd), path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or (info.st_dev, info.st_ino) != (target.st_dev, target.st_ino):
                raise ValueError("RETENTION_FILE_ALIAS_FORBIDDEN")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._repair_archive_checkpoint()
        except BaseException:
            os.close(fd)
            raise
        return fd

    def _repair_archive_checkpoint(self, *, required_receipt=None):
        """Recover a published receipt whose head update was interrupted.

        The immutable receipt sequence is the durable high-water: a restored
        checkpoint is advanced along its exact chain, never reused for a fork.
        Missing/conflicting receipts fail closed before any rotation.
        """
        head = self._archive_checkpoint()
        paths = []
        for count, path in enumerate(self.archive_root.iterdir(), 1):
            if count > self.archive_maximum_files:
                raise ValueError("RETENTION_ARCHIVE_INVENTORY_LIMIT")
            if re.fullmatch(r"[0-9a-f]{32}\.receipt\.json", path.name):
                paths.append(path)
        if len(paths) == head["receipt_count"] and required_receipt is None:
            if paths:
                receipt, _ = self._read_control(self.archive_root / (head["generation_id"] + ".receipt.json"))
                if digest(receipt) != head["receipt_digest"] or receipt.get("receipt_sequence") != head["receipt_count"]:
                    raise ValueError("RETENTION_ARCHIVE_CHECKPOINT_INVALID")
            return head
        if len(paths) < head["receipt_count"]:
            raise ValueError("RETENTION_ARCHIVE_RECEIPT_MISSING")
        # Retain bounded chain metadata, not all receipt bodies. Individual
        # control files may reach CONTROL_LIMIT even when the archive is full.
        chain = []
        for path in paths:
            receipt, _ = self._read_control(path)
            if (type(receipt.get("receipt_sequence")) is not int
                    or _generation_id(receipt.get("generation_id")) + ".receipt.json" != path.name):
                raise ValueError("RETENTION_ARCHIVE_RECEIPT_INVALID")
            chain.append((receipt["receipt_sequence"], digest(receipt), receipt.get("previous_receipt_digest"), path))
        previous, required_found = None, required_receipt is None
        for count, (sequence, receipt_hash, previous_hash, path) in enumerate(sorted(chain, key=lambda row: row[0]), 1):
            if sequence != count or previous_hash != previous:
                raise ValueError("RETENTION_ARCHIVE_RECEIPT_LINEAGE_CONFLICT")
            previous = receipt_hash
            if required_receipt is not None and count == required_receipt["receipt_sequence"]:
                if receipt_hash != digest(required_receipt):
                    raise ValueError("RETENTION_ARCHIVE_RECEIPT_LINEAGE_CONFLICT")
                required_found = True
            if count == head["receipt_count"] and previous != head["receipt_digest"]:
                raise ValueError("RETENTION_ARCHIVE_CHECKPOINT_INVALID")
            if count > head["receipt_count"]:
                receipt, _ = self._read_control(path)
                if digest(receipt) != receipt_hash:
                    raise ValueError("RETENTION_ARCHIVE_RECEIPT_LINEAGE_CONFLICT")
                self._verify_archive(receipt)
                self._advance_archive_checkpoint(receipt)
        if not required_found:
            raise ValueError("RETENTION_ARCHIVE_RECEIPT_LINEAGE_CONFLICT")
        return self._archive_checkpoint()

    def _archive_checkpoint(self):
        if self.archive_root is None:
            raise ValueError("RETENTION_ARCHIVE_DESTINATION_UNCONFIGURED")
        path = self.archive_root / "CHECKPOINT.json"
        if not path.exists():
            return {"schema": CHECKPOINT_SCHEMA, "receipt_count": 0, "receipt_digest": None}
        value, _ = self._read_control(path)
        if (value.get("schema") != CHECKPOINT_SCHEMA or type(value.get("receipt_count")) is not int
                or value["receipt_count"] <= 0 or not HEX.fullmatch(str(value.get("receipt_digest", "")))
                or value.get("digest") != digest({key: item for key, item in value.items() if key != "digest"})):
            raise ValueError("RETENTION_ARCHIVE_CHECKPOINT_INVALID")
        return value

    def _advance_archive_checkpoint(self, receipt):
        head = self._archive_checkpoint()
        receipt_hash = digest(receipt)
        if head["receipt_count"] == receipt["receipt_sequence"] and head["receipt_digest"] == receipt_hash:
            return head
        if head["receipt_count"] > receipt["receipt_sequence"]:
            # An older durable receipt can need its local ACK after another
            # archive advances HEAD. Prove chain membership without rewinding
            # the receipt high-water, then regenerate that exact ACK.
            return self._repair_archive_checkpoint(required_receipt=receipt)
        if (head["receipt_count"] + 1 != receipt["receipt_sequence"]
                or head["receipt_digest"] != receipt["previous_receipt_digest"]):
            raise ValueError("RETENTION_ARCHIVE_RECEIPT_LINEAGE_CONFLICT")
        value = {"schema": CHECKPOINT_SCHEMA, "receipt_count": receipt["receipt_sequence"],
                 "receipt_digest": receipt_hash, "generation_id": receipt["generation_id"],
                 "previous_receipt_digest": receipt["previous_receipt_digest"],
                 "custody": "LOCAL_PRIVATE_FSYNC_NOT_WORM"}
        value["digest"] = digest(value)
        self._durable_control(self.archive_root / "CHECKPOINT.json", value)
        return value

    def _verify_archive(self, receipt):
        if (self.archive_root is None or receipt.get("schema") != ARCHIVE_SCHEMA
                or receipt.get("archiver_id") != ARCHIVER_ID or receipt.get("archive_verified") is not True
                or receipt.get("durable") is not True or type(receipt.get("receipt_sequence")) is not int
                or receipt["receipt_sequence"] <= 0 or not HEX.fullmatch(str(receipt.get("archive_sha256", "")))
                or not HEX.fullmatch(str(receipt.get("manifest_sha256", "")))):
            raise ValueError("RETENTION_ARCHIVE_RECEIPT_INVALID")
        ident = _generation_id(receipt["generation_id"])
        object_name = ident + ".tar.gz"
        if receipt.get("archive_uri") != "local-private://" + object_name or stamp(receipt["acknowledged_at"]) > datetime.now(timezone.utc):
            raise ValueError("RETENTION_ARCHIVE_RECEIPT_INVALID")
        external, _ = self._read_control(self.archive_root / (ident + ".receipt.json"))
        if receipt != external:
            raise ValueError("RETENTION_ARCHIVE_RECEIPT_MISMATCH")
        path = self.archive_root / object_name
        if self._file_hash(path, maximum_bytes=self.archive_maximum_bytes) != receipt["archive_sha256"]:
            raise ValueError("RETENTION_ARCHIVE_OBJECT_HASH_MISMATCH")
        hashes, manifest_raw, total = {}, None, 0
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream, gzip.GzipFile(fileobj=stream) as decoded:
            with tarfile.open(fileobj=decoded, mode="r|") as archive:
                for member in archive:
                    if member.name not in MEMBERS or member.name in hashes or not member.isfile():
                        raise ValueError("RETENTION_ARCHIVE_OBJECT_SHAPE_INVALID")
                    total += member.size
                    if member.size < 0 or total > self.policy.maximum_bytes:
                        raise ValueError("RETENTION_ARCHIVE_OBJECT_PAYLOAD_LIMIT")
                    source = archive.extractfile(member)
                    hasher, parts = hashlib.sha256(), []
                    while True:
                        chunk = source.read(65536)
                        if not chunk:
                            break
                        hasher.update(chunk)
                        if member.name == "manifest.json":
                            if sum(map(len, parts)) + len(chunk) > CONTROL_LIMIT:
                                raise ValueError("RETENTION_CONTROL_LIMIT")
                            parts.append(chunk)
                    hashes[member.name] = hasher.hexdigest()
                    if member.name == "manifest.json":
                        manifest_raw = b"".join(parts)
            # Consume bounded tar padding and validate the gzip trailer/CRC.
            while True:
                padding = decoded.read(65536)
                if decoded.tell() > self.policy.maximum_bytes + 65536:
                    raise ValueError("RETENTION_ARCHIVE_OBJECT_PAYLOAD_LIMIT")
                if not padding:
                    break
                if any(padding):
                    raise ValueError("RETENTION_ARCHIVE_OBJECT_TRAILING_DATA")
        if set(hashes) not in (BASE_MEMBERS, MEMBERS) or hashes["manifest.json"] != receipt["manifest_sha256"]:
            raise ValueError("RETENTION_ARCHIVE_MANIFEST_MISMATCH")
        manifest = json.loads(manifest_raw, object_pairs_hook=_unique_object)
        if not isinstance(manifest, dict) or manifest.get("generation_id") != ident or manifest.get("schema") not in {
                "rc6.shadow-evidence-generation.v1", "rc6.shadow-evidence-generation.v2"}:
            raise ValueError("RETENTION_ARCHIVE_MANIFEST_MISMATCH")
        expected = {"report": "report.json.gz", "checkpoint": "checkpoint.json.gz", "status": "status.json"}
        if "projection.sqlite" in hashes: expected["projection"] = "projection.sqlite"
        if not isinstance(manifest.get("files"), dict) or set(manifest["files"]) != set(expected):
            raise ValueError("RETENTION_ARCHIVE_MANIFEST_MISMATCH")
        for role, name in expected.items():
            if not isinstance(manifest["files"][role], dict) or manifest["files"][role].get("name") != name or manifest["files"][role].get("sha256") != hashes[name]:
                raise ValueError("RETENTION_ARCHIVE_MEMBER_HASH_MISMATCH")
        return manifest

    def _archive_generation(self, path):
        ident = _generation_id(path.name)
        self._archive_inventory()
        external_path = self.archive_root / (ident + ".receipt.json")
        if external_path.exists():
            receipt, _ = self._read_control(external_path)
            self._verify_archive(receipt)
            self._advance_archive_checkpoint(receipt)
            self._durable_control(self.root / ("archive-ack-" + ident + ".json"), receipt)
            return receipt
        members = set(os.listdir(path))
        if members not in (BASE_MEMBERS, MEMBERS):
            raise ValueError("RETENTION_UNOWNED_TEMP_CONTENT")
        manifest, manifest_hash = self._read_control(path / "manifest.json")
        # Reserve uncompressed tar, receipt and simultaneous control temporaries.
        logical_size = sum((path / name).lstat().st_size for name in members)
        self._archive_inventory(additional_bytes=logical_size + logical_size // 1000 + 65536, additional_files=4)
        target = self.archive_root / (ident + ".tar.gz")
        temporary = self.archive_root / (".archive-" + ident + ".tmp")
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as destination:
                with gzip.GzipFile(fileobj=destination, mode="wb", mtime=0) as compressed:
                    with tarfile.open(fileobj=compressed, mode="w|") as archive:
                        for name in sorted(members):
                            source_fd = os.open(path / name, os.O_RDONLY | os.O_NOFOLLOW)
                            with os.fdopen(source_fd, "rb") as source:
                                info = os.fstat(source.fileno())
                                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                                    raise ValueError("RETENTION_FILE_ALIAS_FORBIDDEN")
                                member = tarfile.TarInfo(name); member.size = info.st_size; member.mode = 0o600
                                archive.addfile(member, source)
                destination.flush(); os.fsync(destination.fileno())
            self._fault("archive_before_publish_object")
            if target.exists() or target.is_symlink():
                # A killed unacknowledged object is verified against the newly
                # rebuilt exact object before reuse; never blindly overwritten.
                if self._file_hash(target, maximum_bytes=self.archive_maximum_bytes) != self._file_hash(temporary, maximum_bytes=self.archive_maximum_bytes):
                    raise ValueError("RETENTION_ARCHIVE_OBJECT_CONFLICT")
                temporary.unlink()
            else:
                temporary.rename(target)
            self._sync_directory(self.archive_root)
            head = self._archive_checkpoint()
            receipt = {"schema": ARCHIVE_SCHEMA, "generation_id": ident, "sequence": manifest["sequence"],
                "manifest_sha256": manifest_hash, "archive_sha256": self._file_hash(target, maximum_bytes=self.archive_maximum_bytes),
                "archive_uri": "local-private://" + target.name, "archiver_id": ARCHIVER_ID,
                "archive_verified": True, "durable": True, "acknowledged_at": datetime.now(timezone.utc).isoformat(),
                "receipt_sequence": head["receipt_count"] + 1, "previous_receipt_digest": head["receipt_digest"]}
            self._durable_control(external_path, receipt)
            self._verify_archive(receipt)
            self._advance_archive_checkpoint(receipt)
            self._fault("archive_before_publish_ack")
            self._durable_control(self.root / ("archive-ack-" + ident + ".json"), receipt)
            return receipt
        finally:
            temporary.unlink(missing_ok=True)

    def archive_generation(self, path):
        """Offline/local archiver API; exclusive ownership, no external service."""
        lock = os.open(self.root / "writer.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        archive_lock = None
        try:
            info = os.fstat(lock)
            if info.st_nlink != 1 or not stat.S_ISREG(info.st_mode):
                raise ValueError("RETENTION_FILE_ALIAS_FORBIDDEN")
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            archive_lock = self._lock_archive()
            path = Path(path).absolute()
            if path.parent != self.root or not GENERATION.fullmatch(path.name) or path.is_symlink():
                raise ValueError("RETENTION_GENERATION_ID_INVALID")
            return self._archive_generation(path)
        finally:
            if archive_lock is not None:
                os.close(archive_lock)
            os.close(lock)

    def _inventory(self):
        entries, bytes_used, queue = {}, 0, [(self.root, 0)]
        inspection_limit = max(2048, self.policy.maximum_files * 4)
        while queue:
            directory, depth = queue.pop()
            if depth > 4:
                raise RetentionPressure("RETENTION_INVENTORY_DEPTH", {})
            for path in directory.iterdir():
                info = path.lstat()
                if (stat.S_ISLNK(info.st_mode) or
                        not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)) or
                        (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)):
                    raise RetentionPressure("RETENTION_FILE_ALIAS_FORBIDDEN", {})
                entries[path] = info
                # Count directory metadata too: recursive quotas must include
                # peak staging and cannot be evaded with nested/empty dirs.
                bytes_used += info.st_size
                if len(entries) > inspection_limit:
                    raise RetentionPressure("RETENTION_INVENTORY_LIMIT", {
                        "files": len(entries), "bytes": bytes_used})
                if stat.S_ISDIR(info.st_mode):
                    queue.append((path, depth + 1))
        return entries, bytes_used

    def _read_control(self, path):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("RETENTION_FILE_ALIAS_FORBIDDEN")
            if info.st_size > CONTROL_LIMIT:
                raise ValueError("RETENTION_CONTROL_LIMIT")
            chunks, remaining = [], CONTROL_LIMIT + 1
            while remaining:
                part = os.read(fd, min(remaining, 65536))
                if not part:
                    break
                chunks.append(part)
                remaining -= len(part)
            raw = b"".join(chunks)
            if len(raw) > CONTROL_LIMIT:
                raise ValueError("RETENTION_CONTROL_LIMIT")
            value = json.loads(raw, object_pairs_hook=_unique_object)
            if not isinstance(value, dict):
                raise ValueError("RETENTION_CONTROL_SHAPE")
            return value, hashlib.sha256(raw).hexdigest()
        finally:
            os.close(fd)

    def _pins(self, supplied):
        pins = {_generation_id(value) for value in supplied}
        current = self.root / "CURRENT.json"
        if current.exists():
            pointer, _ = self._read_control(current)
            if "payload" in pointer:
                if pointer.get("digest") != digest(pointer["payload"]):
                    raise ValueError("RETENTION_CURRENT_INVALID")
                pointer = pointer["payload"]
            elif pointer.get("digest") != digest({key: value for key, value in pointer.items() if key != "digest"}):
                raise ValueError("RETENTION_CURRENT_INVALID")
            pins.add(_generation_id(pointer["generation_id"]))
            if not (self.root / ("gen-" + _generation_id(pointer["generation_id"]))).is_dir():
                raise ValueError("RETENTION_CURRENT_INVALID")
            prior = pointer.get("previous_generation_id")
            if prior:
                pins.add(_generation_id(prior))
            previous = pointer.get("previous")
            if isinstance(previous, dict) and previous.get("generation_id"):
                pins.add(_generation_id(previous["generation_id"]))
        registry = self.root / "retention-pins.json"
        if registry.exists():
            value, _ = self._read_control(registry)
            if (value.get("schema") != "RC6_SHADOW_EVIDENCE_PINS_V1" or
                    not isinstance(value.get("generation_ids"), list) or
                    len(value["generation_ids"]) > self.policy.maximum_files):
                raise ValueError("RETENTION_PIN_REGISTRY_INVALID")
            pins.update(_generation_id(value) for value in value["generation_ids"])
        return pins

    def _remove_flat_directory(self, path, *, complete=False):
        # Never recurse with shutil.rmtree. The accepted namespace and regular
        # member set are narrow; NOFOLLOW prevents aliases at deletion time.
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            names = set(os.listdir(fd))
            if not names <= MEMBERS or (complete and names not in (BASE_MEMBERS, MEMBERS)):
                raise ValueError("RETENTION_UNOWNED_TEMP_CONTENT")
            for name in names:
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError("RETENTION_FILE_ALIAS_FORBIDDEN")
            for name in sorted(names):
                self._fault("rotation_before_unlink_" + name)
                os.unlink(name, dir_fd=fd)
                self._fault("rotation_after_unlink_" + name)
            self._fault("rotation_before_directory_fsync")
            os.fsync(fd)
            self._fault("rotation_after_directory_fsync")
        finally:
            os.close(fd)
        path.rmdir()

    def _acknowledged(self, path):
        generation_id = GENERATION.fullmatch(path.name)[1]
        ack_path = self.root / f"archive-ack-{generation_id}.json"
        if not ack_path.exists():
            return False
        try:
            ack, _ = self._read_control(ack_path)
            manifest, manifest_hash = self._read_control(path / "manifest.json")
            accepted = (ack.get("schema") == ARCHIVE_SCHEMA and
                ack.get("generation_id") == generation_id and
                manifest.get("generation_id") == generation_id and
                ack.get("manifest_sha256") == manifest_hash and
                isinstance(ack.get("archive_sha256"), str) and
                HEX.fullmatch(ack["archive_sha256"]) is not None and
                ack["archive_sha256"] != "0" * 64 and
                isinstance(ack.get("archive_uri"), str) and 0 < len(ack["archive_uri"]) <= 2048 and
                ack.get("archive_verified") is True and ack.get("durable") is True and
                stamp(ack["acknowledged_at"]) <= datetime.now(timezone.utc))
            if not accepted or manifest.get("schema") not in {"rc6.shadow-evidence-generation.v1", "rc6.shadow-evidence-generation.v2"}:
                return False
            files = manifest.get("files")
            if not isinstance(files, dict) or set(files) not in ({"report", "checkpoint", "status"}, {"report", "checkpoint", "status", "projection"}):
                return False
            expected_names = {"report": "report.json.gz", "checkpoint": "checkpoint.json.gz", "status": "status.json"}
            if "projection" in files: expected_names["projection"] = "projection.sqlite"
            for role, expected_name in expected_names.items():
                member = files[role]
                if (not isinstance(member, dict) or member.get("name") != expected_name or
                        not isinstance(member.get("sha256"), str) or not HEX.fullmatch(member["sha256"]) or
                        self._file_hash(path / expected_name) != member["sha256"]):
                    return False
            archived_manifest = self._verify_archive(ack)
            if archived_manifest != manifest:
                return False
            self._advance_archive_checkpoint(ack)
            return True
        except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, EOFError):
            return False

    def _file_hash(self, path, *, maximum_bytes=None):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                    info.st_size > (self.policy.maximum_bytes if maximum_bytes is None else maximum_bytes)):
                raise ValueError("RETENTION_ARCHIVE_MEMBER_INVALID")
            hasher = hashlib.sha256()
            while True:
                part = os.read(fd, 65536)
                if not part:
                    return hasher.hexdigest()
                hasher.update(part)
        finally:
            os.close(fd)

    def _compact_ack(self, ident):
        ack_path = self.root / ("archive-ack-" + ident + ".json")
        if not ack_path.exists():
            return
        receipt, _ = self._read_control(ack_path)
        self._verify_archive(receipt)
        head = self._advance_archive_checkpoint(receipt)
        # The immutable external receipt carries its previous receipt digest;
        # a single bounded checkpoint replaces high-cadence local ACK files.
        self._durable_control(self.root / "archive-checkpoint.json", head)
        self._fault("rotation_before_compact_ack")
        ack_path.unlink()
        self._sync_root()
        self._fault("rotation_after_compact_ack")

    def _rotate(self, path):
        ident = _generation_id(path.name)
        members = set(os.listdir(path))
        if members not in (BASE_MEMBERS, MEMBERS):
            raise ValueError("RETENTION_UNOWNED_TEMP_CONTENT")
        for name in members:
            info = (path / name).lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("RETENTION_FILE_ALIAS_FORBIDDEN")
        receipt, _ = self._read_control(self.root / ("archive-ack-" + ident + ".json"))
        self._verify_archive(receipt)
        self._advance_archive_checkpoint(receipt)
        tombstone = self.root / (".deleting-" + ident)
        intent = self.root / ("delete-intent-" + ident + ".json")
        self._durable_control(intent, {"schema": "RC6_SHADOW_DELETE_INTENT_V2", "generation_id": ident,
            "receipt_digest": digest(receipt), "manifest_sha256": receipt["manifest_sha256"]})
        self._fault("rotation_before_rename")
        path.rename(tombstone)
        self._fault("rotation_after_rename")
        self._sync_root()
        self._fault("rotation_after_rename_fsync")
        self._remove_flat_directory(tombstone)
        self._sync_root()
        self._fault("rotation_after_remove_directory")
        self._compact_ack(ident)
        intent.unlink()
        self._sync_root()

    def _recover_deletions(self, pins):
        recovered = 0
        for intent in sorted(self.root.glob("delete-intent-*.json")):
            ident = _generation_id(intent.name.removeprefix("delete-intent-").removesuffix(".json"))
            value, _ = self._read_control(intent)
            if value.get("schema") != "RC6_SHADOW_DELETE_INTENT_V2" or value.get("generation_id") != ident or ident in pins:
                raise ValueError("RETENTION_DELETE_INTENT_INVALID")
            generation, tombstone = self.root / ("gen-" + ident), self.root / (".deleting-" + ident)
            ack_path = self.root / ("archive-ack-" + ident + ".json")
            if not ack_path.exists() and not generation.exists() and not tombstone.exists():
                # Crash after the ACK's durable compaction, before intent unlink.
                receipt, _ = self._read_control(self.archive_root / (ident + ".receipt.json"))
                self._verify_archive(receipt)
            else:
                receipt, _ = self._read_control(ack_path)
                self._verify_archive(receipt)
            if digest(receipt) != value.get("receipt_digest") or receipt["manifest_sha256"] != value.get("manifest_sha256"):
                raise ValueError("RETENTION_DELETE_INTENT_INVALID")
            self._advance_archive_checkpoint(receipt)
            if generation.exists() and tombstone.exists():
                raise ValueError("RETENTION_DELETE_NAMESPACE_CONFLICT")
            if generation.exists():
                if not self._acknowledged(generation):
                    raise ValueError("RETENTION_ARCHIVE_RECEIPT_INVALID")
                generation.rename(tombstone)
                self._sync_root()
            if tombstone.exists():
                self._remove_flat_directory(tombstone)
                self._sync_root()
            self._compact_ack(ident)
            intent.unlink(); self._sync_root()
            recovered += 1
        # A tombstone without its durable intent is never deletable authority.
        if any(self.root.glob(".deleting-*")):
            raise ValueError("RETENTION_DELETE_INTENT_MISSING")
        return recovered

    def _metrics(self, entries, size, *, additional_bytes, additional_files, pins,
                 cleaned, rotated, filesystem_available):
        projected_bytes, projected_files = size + additional_bytes, len(entries) + additional_files
        pressure = (projected_bytes >= self.policy.maximum_bytes * self.policy.soft_ratio or
                    projected_files >= self.policy.maximum_files * self.policy.soft_ratio)
        return {"schema": "RC6_SHADOW_RETENTION_V1", "status": "RETENTION_PRESSURE" if pressure else "OK",
            "reason": "RETENTION_SOFT_THRESHOLD" if pressure else "WITHIN_QUOTA",
            "shadow_degraded": False, "files": len(entries), "bytes": size,
            "projected_files": projected_files, "projected_bytes": projected_bytes,
            "maximum_files": self.policy.maximum_files, "maximum_bytes": self.policy.maximum_bytes,
            "soft_ratio": self.policy.soft_ratio, "filesystem_available_bytes": filesystem_available,
            "control_reserve_bytes": self.policy.control_reserve_bytes,
            "cleaned_temporaries": cleaned, "rotated_archived_generations": rotated,
            "pinned_generation_count": len(pins),
            "tick_seconds": 30, "contracted_horizon_seconds": CONTRACTED_HORIZON_SECONDS,
            "minutes_to_hard_files": max(0, (self.policy.maximum_files - projected_files) / 5 * .5),
            "minutes_to_hard_bytes_at_current_cut": (None if additional_bytes <= 0 else
                max(0, (self.policy.maximum_bytes - projected_bytes) / additional_bytes * .5)),
            "archive_configured": self.archive_root is not None,
            "archive_custody": "LOCAL_PRIVATE_FSYNC_NOT_WORM" if self.archive_root is not None else "UNCONFIGURED",
            "rotation_authority": "DURABLE_VERIFIED_ARCHIVE_ACK_AND_NOT_PINNED",
            "quota_basis": "RECURSIVE_ENTRIES_AND_LOGICAL_BYTES_INCLUDING_STAGING_PEAK",
            "factual_paths_effect": "NONE", "real_orders_sent": 0, "real_routes": "NOT_CALLED"}

    def prepare(self, *, additional_bytes=0, additional_files=0, pinned=(), writer_fd=None):
        """Acquire writer ownership or verify the caller's existing lock handle.

        Reacquiring the same open file description preserves EvidenceFiles'
        lock; it is never unlocked by this helper. A standalone caller acquires
        a new lock without waiting, so live staging is never cleaned under an
        active writer. Both paths count writer.lock as durable control data.
        """
        lock_path = self.root / "writer.lock"
        owned = writer_fd is None
        fd = None
        archive_lock = None
        try:
            fd = (os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                if owned else writer_fd)
            info, target = os.fstat(fd), lock_path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                    stat.S_ISLNK(target.st_mode) or (info.st_dev, info.st_ino) != (target.st_dev, target.st_ino)):
                raise RetentionPressure("RETENTION_WRITER_LOCK_ALIAS_INVALID", {})
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RetentionPressure("RETENTION_WRITER_BUSY", {}) from exc
            archive_lock = self._lock_archive()
            return self._prepare(additional_bytes=additional_bytes, additional_files=additional_files, pinned=pinned)
        except OSError as exc:
            reason = ("RETENTION_NO_SPACE" if exc.errno in {errno.ENOSPC, errno.EDQUOT} else
                "RETENTION_PERMISSION_DENIED" if exc.errno in {errno.EACCES, errno.EPERM} else
                "RETENTION_IO_UNAVAILABLE")
            raise RetentionPressure(reason, {"error_class": type(exc).__name__}) from exc
        finally:
            if archive_lock is not None:
                os.close(archive_lock)
            if owned and fd is not None:
                os.close(fd)

    def _prepare(self, *, additional_bytes=0, additional_files=0, pinned=()):
        """Clean own uncommitted temps, rotate ACKed evidence, then admit a peak.

        Admission never assumes an overwrite freed the old generation. File
        sizes are logical bytes, so sparse files cannot evade the quota. The
        caller remains responsible for pessimistic new directory/control bytes.
        """
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0
               for value in (additional_bytes, additional_files)):
            raise ValueError("INVALID_RETENTION_PROJECTION")
        entries, size = self._inventory()
        try:
            pins = self._pins(pinned)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise RetentionPressure("RETENTION_PIN_OR_CURRENT_INVALID", {
                "files": len(entries), "bytes": size}) from exc
        cleaned, rotated = 0, 0
        try:
            rotated += self._recover_deletions(pins)
            for path in sorted(entries):
                if path.parent != self.root:
                    continue
                if STAGING.fullmatch(path.name) and path.is_dir():
                    # A committed CURRENT must never point at staging.
                    if STAGING.fullmatch(path.name)[1] not in pins:
                        self._remove_flat_directory(path)
                        cleaned += 1
                elif OWN_TEMP.fullmatch(path.name) and path.is_file():
                    path.unlink()
                    cleaned += 1
        except (OSError, ValueError, TypeError, tarfile.TarError, EOFError) as exc:
            raise RetentionPressure("RETENTION_TEMP_CLEANUP_UNSAFE", {
                "files": len(entries), "bytes": size, "cleaned_temporaries": cleaned}) from exc
        if cleaned or rotated:
            self._sync_root()
            entries, size = self._inventory()
        space = os.statvfs(self.root)
        available = space.f_bavail * space.f_frsize
        metrics = self._metrics(entries, size, additional_bytes=additional_bytes,
            additional_files=additional_files, pins=pins, cleaned=cleaned,
            rotated=rotated, filesystem_available=available)
        if metrics["status"] == "RETENTION_PRESSURE" or available < additional_bytes + self.policy.control_reserve_bytes:
            for path in sorted(entries):
                match = GENERATION.fullmatch(path.name) if path.parent == self.root else None
                if not match or match[1] in pins or not path.is_dir():
                    continue
                try:
                    if not self._acknowledged(path) and self.auto_archive and self.archive_root is not None:
                        self._archive_generation(path)
                    if not self._acknowledged(path):
                        continue
                    self._rotate(path)
                except (OSError, ValueError, TypeError, tarfile.TarError, EOFError) as exc:
                    raise RetentionPressure("RETENTION_ARCHIVE_ROTATION_FAILED", metrics) from exc
                rotated += 1
                entries, size = self._inventory()
                space = os.statvfs(self.root)
                available = space.f_bavail * space.f_frsize
                metrics = self._metrics(entries, size, additional_bytes=additional_bytes,
                    additional_files=additional_files, pins=pins, cleaned=cleaned,
                    rotated=rotated, filesystem_available=available)
                if metrics["status"] == "OK" and available >= additional_bytes + self.policy.control_reserve_bytes:
                    break
        if metrics["projected_files"] > self.policy.maximum_files:
            raise RetentionPressure("RETENTION_HARD_FILES_CAPACITY_REACHED", metrics)
        if metrics["projected_bytes"] > self.policy.maximum_bytes:
            raise RetentionPressure("RETENTION_HARD_BYTES_CAPACITY_REACHED", metrics)
        if available < additional_bytes + self.policy.control_reserve_bytes:
            raise RetentionPressure("RETENTION_NO_SPACE", metrics)
        return metrics

    def _sync_root(self):
        self._sync_directory(self.root)

    def _sync_directory(self, path):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
