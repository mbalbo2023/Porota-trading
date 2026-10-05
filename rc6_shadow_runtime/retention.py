"""Quota admission and acknowledged rotation for private SHADOW evidence.

Call prepare only while EvidenceFiles owns its exclusive writer flock. Readers
hold a shared flock, so generation rotation cannot race a committed read. No
trading source, provider, broker, container, or external archive is accessed.
"""
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import fcntl
import errno
import hashlib
import gzip
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import tarfile
import uuid

from rc6_dynamic_universe.common import digest, stamp
from .archive_namespace import inspect_archive, is_archive_member, is_archive_temporary


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
COMPONENT_CHECKPOINT_SCHEMA = "RC6_SHADOW_ARCHIVE_CHECKPOINT_V3"
CONTRACTED_HORIZON_SECONDS = 9 * 60 * 60
RECOVERY_MARGIN_SECONDS = 60 * 60
GC_SCHEMA = "RC6_SHADOW_ARCHIVE_GC_INTENT_V3"
BUILD_SCHEMA = "RC6_SHADOW_ARCHIVE_BUILD_INTENT_V3"


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
                 auto_archive=False, fault_inject=None, archive_format="TAR_V2"):
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
            authority = self.root.parent / (self.root.name + ".authority")
            if self.archive_root == authority or self.archive_root.is_relative_to(authority) or authority.is_relative_to(self.archive_root):
                raise ValueError("RETENTION_ARCHIVE_MUST_BE_SEPARATE")
        if any(type(value) is not int or value <= 0 for value in (archive_maximum_bytes, archive_maximum_files)):
            raise ValueError("INVALID_RETENTION_ARCHIVE_POLICY")
        self.archive_maximum_bytes, self.archive_maximum_files = archive_maximum_bytes, archive_maximum_files
        if not isinstance(archive_format, str) or archive_format not in {"TAR_V2", "COMPONENT_V3"}:
            raise ValueError("INVALID_RETENTION_ARCHIVE_FORMAT")
        self.archive_format = archive_format
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
        directory = self.archive_root.lstat()
        if not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.getuid() or stat.S_IMODE(directory.st_mode) != 0o700:
            raise ValueError("RETENTION_ARCHIVE_DIRECTORY_CUSTODY_INVALID")
        count, size, allocated = 0, 0, self.archive_root.stat().st_blocks * 512
        for path in self.archive_root.iterdir():
            info = path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600):
                raise ValueError("RETENTION_ARCHIVE_MEMBER_INVALID")
            if is_archive_temporary(path.name):
                path.unlink()
                self._sync_directory(self.archive_root)
                continue
            if not is_archive_member(path.name):
                raise ValueError("RETENTION_ARCHIVE_NAMESPACE_UNKNOWN")
            count += 1; size += info.st_size
            allocated += info.st_blocks * 512
            if count > self.archive_maximum_files:
                raise RetentionPressure("RETENTION_ARCHIVE_FILES_CAPACITY_REACHED", {"archive_files": count})
        physical_extra = additional_bytes + additional_files * 4096
        if self.archive_format == "COMPONENT_V3" and additional_bytes:
            # The fixed cap covers GC intent/control staging too. Maintain
            # reserve before growing the archive, never count deletion first.
            physical_extra += 2 * CONTROL_LIMIT + 8192
            additional_bytes += 2 * CONTROL_LIMIT
            additional_files += 2
        if (count + additional_files > self.archive_maximum_files or size + additional_bytes > self.archive_maximum_bytes
                or allocated + physical_extra > self.archive_maximum_bytes):
            raise RetentionPressure("RETENTION_ARCHIVE_CAPACITY_REACHED", {
                "archive_files": count, "archive_bytes": size,
                "archive_projected_files": count + additional_files, "archive_projected_bytes": size + additional_bytes,
                "archive_allocated_bytes": allocated, "archive_projected_allocated_bytes": allocated+physical_extra,
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
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or (info.st_dev, info.st_ino) != (target.st_dev, target.st_ino)):
                raise ValueError("RETENTION_FILE_ALIAS_FORBIDDEN")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._recover_archive_gc()
            self._repair_archive_checkpoint()
            self._recover_archive_build()
        except BaseException:
            os.close(fd)
            raise
        return fd

    def _archive_receipts(self, *, head=None):
        """Return receipt metadata in exact sequence above the durable boundary."""
        head = self._archive_checkpoint() if head is None else head
        boundary = head.get("compacted_receipt_count", 0)
        previous = head.get("compacted_receipt_digest")
        chain, seen = [], set()
        for count, path in enumerate(self._read_only_paths(self.archive_root), 1):
            if count > self.archive_maximum_files:
                raise ValueError("RETENTION_ARCHIVE_INVENTORY_LIMIT")
            if not re.fullmatch(r"[0-9a-f]{32}\.receipt\.json", path.name):
                continue
            receipt, _ = self._read_control(path)
            self._receipt_header(receipt)
            if _generation_id(receipt.get("generation_id")) + ".receipt.json" != path.name:
                raise ValueError("RETENTION_ARCHIVE_RECEIPT_INVALID")
            sequence = receipt["receipt_sequence"]
            if sequence <= boundary:
                # Only an in-flight durable GC intent may leave this prefix.
                if not (self.archive_root / "GC.json").exists():
                    raise ValueError("RETENTION_ARCHIVE_EXPIRED_RECEIPT_REAPPEARED")
                continue
            if sequence in seen:
                raise ValueError("RETENTION_ARCHIVE_RECEIPT_LINEAGE_CONFLICT")
            seen.add(sequence); chain.append(receipt)
        for offset, receipt in enumerate(sorted(chain, key=lambda row: row["receipt_sequence"]), boundary + 1):
            if receipt["receipt_sequence"] != offset or receipt.get("previous_receipt_digest") != previous:
                raise ValueError("RETENTION_ARCHIVE_RECEIPT_LINEAGE_CONFLICT")
            previous = digest(receipt)
            if offset == head["receipt_count"] and previous != head["receipt_digest"]:
                raise ValueError("RETENTION_ARCHIVE_CHECKPOINT_INVALID")
        if len(chain) + boundary < head["receipt_count"]:
            raise ValueError("RETENTION_ARCHIVE_RECEIPT_MISSING")
        return sorted(chain, key=lambda row: row["receipt_sequence"])

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
        boundary = head.get("compacted_receipt_count", 0)
        if len(paths) == head["receipt_count"] - boundary and required_receipt is None:
            if paths:
                receipt, _ = self._read_control(self.archive_root / (head["generation_id"] + ".receipt.json"))
                if digest(receipt) != head["receipt_digest"] or receipt.get("receipt_sequence") != head["receipt_count"]:
                    raise ValueError("RETENTION_ARCHIVE_CHECKPOINT_INVALID")
            return head
        if len(paths) < head["receipt_count"] - boundary:
            raise ValueError("RETENTION_ARCHIVE_RECEIPT_MISSING")
        # Retain bounded chain metadata, not all receipt bodies. Individual
        # control files may reach CONTROL_LIMIT even when the archive is full.
        chain = []
        for path in paths:
            receipt, _ = self._read_control(path)
            self._receipt_header(receipt)
            if (type(receipt.get("receipt_sequence")) is not int
                    or _generation_id(receipt.get("generation_id")) + ".receipt.json" != path.name):
                raise ValueError("RETENTION_ARCHIVE_RECEIPT_INVALID")
            chain.append((receipt["receipt_sequence"], digest(receipt), receipt.get("previous_receipt_digest"), path))
        previous, required_found = head.get("compacted_receipt_digest"), required_receipt is None
        for count, (sequence, receipt_hash, previous_hash, path) in enumerate(sorted(chain, key=lambda row: row[0]), boundary + 1):
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
        if (not isinstance(value.get("schema"), str) or value.get("schema") not in {CHECKPOINT_SCHEMA, COMPONENT_CHECKPOINT_SCHEMA} or type(value.get("receipt_count")) is not int
                or value["receipt_count"] <= 0 or not HEX.fullmatch(str(value.get("receipt_digest", "")))
                or value.get("digest") != digest({key: item for key, item in value.items() if key != "digest"})):
            raise ValueError("RETENTION_ARCHIVE_CHECKPOINT_INVALID")
        if value["schema"] == COMPONENT_CHECKPOINT_SCHEMA:
            boundary = value.get("compacted_receipt_count")
            if (type(boundary) is not int or not 0 <= boundary < value["receipt_count"]
                    or (boundary == 0 and value.get("compacted_receipt_digest") is not None)
                    or (boundary > 0 and not HEX.fullmatch(str(value.get("compacted_receipt_digest", ""))))
                    or value.get("contracted_horizon_seconds") != CONTRACTED_HORIZON_SECONDS
                    or value.get("recovery_margin_seconds") != RECOVERY_MARGIN_SECONDS):
                raise ValueError("RETENTION_ARCHIVE_CHECKPOINT_INVALID")
            for key in ("source_as_of_max", "expired_before", "last_gc_as_of"):
                if value.get(key) is not None:
                    stamp(value[key])
        return value

    def _receipt_header(self, receipt):
        from .archive_components import ACK_SCHEMA as V3_SCHEMA, ARCHIVER_ID as V3_ARCHIVER
        if (not isinstance(receipt, dict) or not isinstance(receipt.get("schema"), str) or receipt.get("schema") not in {ARCHIVE_SCHEMA, V3_SCHEMA}
                or receipt.get("archiver_id") != (V3_ARCHIVER if receipt.get("schema") == V3_SCHEMA else ARCHIVER_ID)
                or receipt.get("archive_verified") is not True or receipt.get("durable") is not True
                or type(receipt.get("receipt_sequence")) is not int or receipt["receipt_sequence"] <= 0
                or type(receipt.get("sequence")) is not int or receipt["sequence"] <= 0
                or not HEX.fullmatch(str(receipt.get("archive_sha256", "")))
                or not HEX.fullmatch(str(receipt.get("manifest_sha256", "")))
                or receipt.get("previous_receipt_digest") is not None and not HEX.fullmatch(str(receipt["previous_receipt_digest"]))):
            raise ValueError("RETENTION_ARCHIVE_RECEIPT_INVALID")
        ident = _generation_id(receipt.get("generation_id"))
        suffix = ".recipe.gz" if receipt["schema"] == V3_SCHEMA else ".tar.gz"
        if (receipt.get("archive_uri") != "local-private://" + ident + suffix
                or stamp(receipt.get("acknowledged_at")) > datetime.now(timezone.utc)):
            raise ValueError("RETENTION_ARCHIVE_RECEIPT_INVALID")
        return ident

    def _advance_archive_checkpoint(self, receipt):
        head = self._archive_checkpoint()
        self._receipt_header(receipt)
        if receipt["receipt_sequence"] <= head.get("compacted_receipt_count", 0):
            raise ValueError("RETENTION_ARCHIVE_GENERATION_EXPIRED")
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
        from .archive_components import ACK_SCHEMA as V3_SCHEMA
        component = head["schema"] == COMPONENT_CHECKPOINT_SCHEMA or receipt["schema"] == V3_SCHEMA
        value = {"schema": COMPONENT_CHECKPOINT_SCHEMA if component else CHECKPOINT_SCHEMA, "receipt_count": receipt["receipt_sequence"],
                 "receipt_digest": receipt_hash, "generation_id": receipt["generation_id"],
                 "previous_receipt_digest": receipt["previous_receipt_digest"],
                 "custody": "LOCAL_PRIVATE_FSYNC_NOT_WORM"}
        if component:
            prior = head.get("source_as_of_max")
            current = receipt.get("source_as_of")
            if current is None:
                current = self._verify_archive(receipt).get("as_of")
            latest = max((stamp(item) for item in (prior, current) if item is not None), default=None)
            value.update(compacted_receipt_count=head.get("compacted_receipt_count", 0),
                compacted_receipt_digest=head.get("compacted_receipt_digest"),
                source_as_of_max=latest.isoformat() if latest is not None else None,
                expired_before=head.get("expired_before"), last_gc_as_of=head.get("last_gc_as_of"),
                contracted_horizon_seconds=CONTRACTED_HORIZON_SECONDS,
                recovery_margin_seconds=RECOVERY_MARGIN_SECONDS)
        value["digest"] = digest(value)
        self._durable_control(self.archive_root / "CHECKPOINT.json", value)
        return value

    def _verify_archive(self, receipt):
        from .archive_components import ACK_SCHEMA as V3_SCHEMA, ComponentArchive
        self._receipt_header(receipt)
        if receipt["receipt_sequence"] <= self._archive_checkpoint().get("compacted_receipt_count", 0):
            raise ValueError("RETENTION_ARCHIVE_GENERATION_EXPIRED")
        if receipt.get("schema") == V3_SCHEMA:
            external, _ = self._read_control(self.archive_root / (receipt["generation_id"] + ".receipt.json"))
            if external != receipt:
                raise ValueError("RETENTION_ARCHIVE_RECEIPT_MISMATCH")
            _, manifest = ComponentArchive(self).restore(receipt)
            if receipt.get("source_as_of") != manifest.get("as_of"):
                raise ValueError("RETENTION_ARCHIVE_MANIFEST_MISMATCH")
            return manifest
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
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NOATIME", 0))
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
            self._clear_archive_build(receipt)
            return receipt
        members = {member.name for member in self._read_only_paths(path)}
        if members not in (BASE_MEMBERS, MEMBERS):
            raise ValueError("RETENTION_UNOWNED_TEMP_CONTENT")
        manifest, manifest_hash = self._read_control(path / "manifest.json")
        head = self._archive_checkpoint()
        if (head.get("expired_before") is not None
                and stamp(manifest.get("as_of")) < stamp(head["expired_before"])):
            raise ValueError("RETENTION_ARCHIVE_GENERATION_OUTSIDE_RETAINED_HORIZON")
        pending = self._build_intent()
        if self.archive_format == "COMPONENT_V3" or pending is not None and pending["generation_id"] == ident:
            from .archive_components import ComponentArchive
            receipt = ComponentArchive(self).build(path, manifest, manifest_hash)
            receipt.update(archive_verified=True, durable=True,
                acknowledged_at=datetime.now(timezone.utc).isoformat(),
                previous_receipt_digest=head["receipt_digest"], source_as_of=manifest.get("as_of"))
            self._durable_control(external_path, receipt)
            self._verify_archive(receipt)
            self._advance_archive_checkpoint(receipt)
            self._archive_inventory()
            self._fault("archive_before_publish_ack")
            self._durable_control(self.root / ("archive-ack-" + ident + ".json"), receipt)
            self._clear_archive_build(receipt)
            self._maintain_archive()
            return receipt
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
                            source_fd = os.open(path / name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NOATIME", 0))
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

    def _archive_pins(self, supplied=()):
        # Every extant source generation is a recovery pin, including a source
        # not yet archived. Expiry cannot race rotation/delete-intent recovery.
        pins = self._pins(supplied)
        for path in self.root.iterdir():
            match = GENERATION.fullmatch(path.name) or DELETING.fullmatch(path.name)
            if match:
                pins.add(match[1])
            match = re.fullmatch(r"(?:archive-ack-|delete-intent-)([0-9a-f]{32})\.json", path.name)
            if match:
                pins.add(match[1])
        return pins

    def _build_intent(self):
        path = self.archive_root / "BUILD.json"
        if not path.exists():
            return None
        value, _ = self._read_control(path)
        if (set(value) != {"schema", "generation_id", "manifest_sha256", "recipe_sha256", "pack_name", "digest"}
                or value.get("schema") != BUILD_SCHEMA
                or value.get("digest") != digest({key: item for key, item in value.items() if key != "digest"})
                or not HEX.fullmatch(str(value.get("manifest_sha256", "")))
                or not HEX.fullmatch(str(value.get("recipe_sha256", "")))
                or value.get("pack_name") is not None and (not isinstance(value["pack_name"], str)
                    or re.fullmatch(r"[0-9a-f]{64}\.cas\.pack", value["pack_name"]) is None)):
            raise ValueError("RETENTION_ARCHIVE_BUILD_INTENT_INVALID")
        _generation_id(value.get("generation_id"))
        return value

    def _write_archive_build_intent(self, ident, manifest_sha, *, recipe_sha256, pack_name):
        prior = self._build_intent()
        if prior is not None and (prior["generation_id"] != ident or prior["manifest_sha256"] != manifest_sha):
            raise ValueError("RETENTION_ARCHIVE_BUILD_IN_PROGRESS")
        value = {"schema": BUILD_SCHEMA, "generation_id": ident, "manifest_sha256": manifest_sha,
                 "recipe_sha256": recipe_sha256, "pack_name": pack_name}
        value["digest"] = digest(value)
        self._durable_control(self.archive_root / "BUILD.json", value)
        self._fault("archive_after_build_intent")

    def _clear_archive_build(self, receipt):
        value = self._build_intent()
        if value is None:
            return
        if (value["generation_id"] != receipt["generation_id"]
                or value["manifest_sha256"] != receipt["manifest_sha256"]
                or value["recipe_sha256"] != receipt["archive_sha256"]):
            raise ValueError("RETENTION_ARCHIVE_BUILD_RECEIPT_CONFLICT")
        self._fault("archive_before_clear_build_intent")
        (self.archive_root / "BUILD.json").unlink()
        self._sync_directory(self.archive_root)

    def _recover_archive_build(self):
        value = self._build_intent()
        if value is None:
            return False
        source = self.root / ("gen-" + value["generation_id"])
        if source.exists():
            manifest, manifest_sha = self._read_control(source / "manifest.json")
            if manifest_sha != value["manifest_sha256"] or manifest.get("generation_id") != value["generation_id"]:
                raise ValueError("RETENTION_ARCHIVE_BUILD_SOURCE_CHANGED")
            self._archive_generation(source)
            return True
        receipt_path = self.archive_root / (value["generation_id"] + ".receipt.json")
        if not receipt_path.exists():
            raise ValueError("RETENTION_ARCHIVE_BUILD_SOURCE_UNAVAILABLE")
        receipt, _ = self._read_control(receipt_path)
        self._verify_archive(receipt)
        self._advance_archive_checkpoint(receipt)
        self._clear_archive_build(receipt)
        return True

    def _recover_archive_gc(self):
        path = self.archive_root / "GC.json"
        if not path.exists():
            return 0
        value, _ = self._read_control(path)
        if (set(value) != {"schema", "previous_checkpoint_digest", "checkpoint", "targets", "digest"}
                or value.get("schema") != GC_SCHEMA
                or value.get("digest") != digest({key: item for key, item in value.items() if key != "digest"})
                or not isinstance(value.get("checkpoint"), dict) or not isinstance(value.get("targets"), list)
                or not 0 < len(value["targets"]) <= 1024):
            raise ValueError("RETENTION_ARCHIVE_GC_INTENT_INVALID")
        checkpoint, head = value["checkpoint"], self._archive_checkpoint()
        if (checkpoint.get("schema") != COMPONENT_CHECKPOINT_SCHEMA
                or checkpoint.get("digest") != digest({key: item for key, item in checkpoint.items() if key != "digest"})
                or checkpoint.get("receipt_count") != head["receipt_count"]
                or checkpoint.get("receipt_digest") != head["receipt_digest"]
                or type(checkpoint.get("compacted_receipt_count")) is not int
                or not head.get("compacted_receipt_count", 0) <= checkpoint["compacted_receipt_count"] < head["receipt_count"]
                or checkpoint.get("contracted_horizon_seconds") != CONTRACTED_HORIZON_SECONDS
                or checkpoint.get("recovery_margin_seconds") != RECOVERY_MARGIN_SECONDS):
            raise ValueError("RETENTION_ARCHIVE_GC_INTENT_INVALID")
        for key in ("expired_before", "last_gc_as_of", "source_as_of_max"):
            if not isinstance(checkpoint.get(key), str):
                raise ValueError("RETENTION_ARCHIVE_GC_INTENT_INVALID")
            stamp(checkpoint[key])
        if (checkpoint.get("compacted_receipt_digest") is None
                or not HEX.fullmatch(str(checkpoint["compacted_receipt_digest"]))
                or stamp(checkpoint["expired_before"]) != stamp(checkpoint["source_as_of_max"]) - timedelta(
                    seconds=CONTRACTED_HORIZON_SECONDS + RECOVERY_MARGIN_SECONDS)
                or checkpoint["last_gc_as_of"] != checkpoint["source_as_of_max"]):
            raise ValueError("RETENTION_ARCHIVE_GC_INTENT_INVALID")
        pins, seen = self._archive_pins(), set()
        for target in value["targets"]:
            if (not isinstance(target, dict) or set(target) != {"name", "sha256"}
                    or not isinstance(target.get("name"), str)
                    or re.fullmatch(r"(?:[0-9a-f]{32}\.(?:recipe\.gz|tar\.gz|receipt\.json)|[0-9a-f]{64}\.cas\.pack)", target["name"]) is None
                    or target["name"] in seen or not HEX.fullmatch(str(target.get("sha256", "")))):
                raise ValueError("RETENTION_ARCHIVE_GC_INTENT_INVALID")
            seen.add(target["name"])
            ident = target["name"].split(".", 1)[0]
            if len(ident) == 32 and ident in pins:
                raise ValueError("RETENTION_ARCHIVE_GC_PIN_CONFLICT")
            candidate = self.archive_root / target["name"]
            if candidate.exists() or candidate.is_symlink():
                from .archive_components import read
                original, _ = read(candidate, maximum=self.archive_maximum_bytes)
                if hashlib.sha256(original).hexdigest() != target["sha256"]:
                    raise ValueError("RETENTION_ARCHIVE_GC_MEMBER_CHANGED")
        # Recovery re-derives reachability from the surviving committed chain,
        # instead of trusting a persisted list or a mutable refcount alone.
        from .archive_components import ACK_SCHEMA as V3_SCHEMA, ComponentArchive
        chain = self._archive_receipts(head=head)
        kept = [receipt for receipt in chain
                if receipt["receipt_sequence"] > checkpoint["compacted_receipt_count"]]
        kept_ids = {receipt["generation_id"] for receipt in kept}
        graph = ComponentArchive(self).dependency_graph([r for r in kept if r["schema"] == V3_SCHEMA])
        kept_ids.update(graph)
        kept_packs = {name for node in graph.values() for name in node["packs"]}
        for target in value["targets"]:
            if target["name"] in kept_packs or target["name"].split(".", 1)[0] in kept_ids:
                raise ValueError("RETENTION_ARCHIVE_GC_REACHABLE_MEMBER")
        if head != checkpoint:
            if head.get("digest") != value["previous_checkpoint_digest"]:
                raise ValueError("RETENTION_ARCHIVE_GC_CHECKPOINT_CONFLICT")
            self._fault("archive_gc_before_checkpoint")
            self._durable_control(self.archive_root / "CHECKPOINT.json", checkpoint)
            self._fault("archive_gc_after_checkpoint")
        for target in value["targets"]:
            self._fault("archive_gc_before_unlink")
            (self.archive_root / target["name"]).unlink(missing_ok=True)
            self._fault("archive_gc_after_unlink")
        self._fault("archive_gc_before_directory_fsync")
        self._sync_directory(self.archive_root)
        self._fault("archive_gc_after_directory_fsync")
        path.unlink(); self._sync_directory(self.archive_root)
        return len(value["targets"])

    def _maintain_archive(self, *, pinned=(), force=False):
        """Expire only a contiguous, unpinned prefix older than wheel+recovery.

        The greatest verified cut clock is the declared retention clock. A
        complete dependency graph preserves transitive page bases, including
        old anchors. Immutable receipts are never rewritten: their compacted
        digest boundary remains in the durable high-water checkpoint.
        """
        from .archive_components import ACK_SCHEMA as V3_SCHEMA, ComponentArchive, read
        head = self._archive_checkpoint()
        if head["schema"] != COMPONENT_CHECKPOINT_SCHEMA or head.get("source_as_of_max") is None:
            return {"expired_generations": 0, "deleted_archive_members": 0}
        latest = stamp(head["source_as_of_max"])
        cutoff = latest - timedelta(seconds=CONTRACTED_HORIZON_SECONDS + RECOVERY_MARGIN_SECONDS)
        if (not force and head.get("last_gc_as_of") is not None
                and latest - stamp(head["last_gc_as_of"]) < timedelta(minutes=15)):
            return {"expired_generations": 0, "deleted_archive_members": 0}
        receipts = self._archive_receipts(head=head)
        # A receipt not yet adopted by HEAD is recovered before any GC call.
        if receipts and receipts[-1]["receipt_sequence"] != head["receipt_count"]:
            raise ValueError("RETENTION_ARCHIVE_GC_UNADOPTED_RECEIPT")
        pins = self._archive_pins(pinned)
        if all(receipt["generation_id"] in pins or receipt.get("source_as_of") is not None
               and stamp(receipt["source_as_of"]) >= cutoff for receipt in receipts):
            # Most of a wheel precedes the first eligible expiration. Do not
            # rebuild its dependency graph on every tick when metadata proves
            # that no receipt can be a deletion candidate.
            value = {key: item for key, item in head.items() if key != "digest"}
            value["last_gc_as_of"] = latest.isoformat(); value["digest"] = digest(value)
            self._durable_control(self.archive_root / "CHECKPOINT.json", value)
            return {"expired_generations": 0, "deleted_archive_members": 0}
        committed_ids = {receipt["generation_id"] for receipt in receipts}
        if any(path.name.removesuffix(".recipe.gz") not in committed_ids
               for path in self.archive_root.glob("*.recipe.gz")):
            raise ValueError("RETENTION_ARCHIVE_UNCOMMITTED_RECIPE_RECOVERY_REQUIRED")
        component = ComponentArchive(self)
        graph = component.dependency_graph([r for r in receipts if r["schema"] == V3_SCHEMA])
        as_of = {}
        for receipt in receipts:
            ident = receipt["generation_id"]
            if receipt["schema"] == V3_SCHEMA:
                as_of[ident] = stamp(graph[ident]["as_of"])
            else:
                as_of[ident] = stamp(self._verify_archive(receipt)["as_of"])
        retained = {receipt["generation_id"] for receipt in receipts
                    if receipt["generation_id"] in pins or as_of[receipt["generation_id"]] >= cutoff}
        for ident in tuple(retained):
            node, seen = graph.get(ident), set()
            while node and node["base_generation_id"] is not None:
                base = node["base_generation_id"]
                if base in seen or base not in graph:
                    raise ValueError("RETENTION_PAGE_DEPENDENCY_CYCLE_OR_DEPTH")
                seen.add(base); retained.add(base); node = graph[base]
        expired = []
        for receipt in receipts:
            if receipt["generation_id"] in retained or receipt["receipt_sequence"] == head["receipt_count"]:
                break
            expired.append(receipt)
            if len(expired) == 128:
                break
        if not expired:
            return {"expired_generations": 0, "deleted_archive_members": 0}
        expired_ids = {receipt["generation_id"] for receipt in expired}
        kept = [r for r in receipts if r["generation_id"] not in expired_ids]
        kept_packs = {name for r in kept if r["schema"] == V3_SCHEMA for name in graph[r["generation_id"]]["packs"]}
        targets = []
        def add(name):
            raw, _ = read(self.archive_root / name, maximum=self.archive_maximum_bytes)
            targets.append({"name": name, "sha256": hashlib.sha256(raw).hexdigest()})
        for receipt in expired:
            ident = receipt["generation_id"]
            add(ident + ".receipt.json")
            add(ident + (".recipe.gz" if receipt["schema"] == V3_SCHEMA else ".tar.gz"))
        for candidate in sorted(self.archive_root.glob("*.cas.pack")):
            if candidate.name not in kept_packs:
                add(candidate.name)
        if len(targets) > 1024:
            # A bounded batch can conservatively retain some unreachable
            # packs; the next batch may collect them. Never drop a live base.
            targets = targets[:1024]
        checkpoint = {key: item for key, item in head.items() if key != "digest"}
        checkpoint.update(compacted_receipt_count=expired[-1]["receipt_sequence"],
            compacted_receipt_digest=digest(expired[-1]), expired_before=cutoff.isoformat(),
            last_gc_as_of=latest.isoformat())
        checkpoint["digest"] = digest(checkpoint)
        value = {"schema": GC_SCHEMA, "previous_checkpoint_digest": head["digest"],
                 "checkpoint": checkpoint, "targets": targets}
        value["digest"] = digest(value)
        self._durable_control(self.archive_root / "GC.json", value)
        self._fault("archive_gc_after_intent")
        removed = self._recover_archive_gc()
        return {"expired_generations": len(expired), "deleted_archive_members": removed}

    def maintain_archive(self, *, pinned=()):
        """Local writer maintenance; verified wheel expiry, no source deletion."""
        lock = os.open(self.root / "writer.lock", os.O_RDWR | os.O_NOFOLLOW)
        archive_lock = None
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            archive_lock = self._lock_archive()
            return self._maintain_archive(pinned=pinned, force=True)
        finally:
            if archive_lock is not None:
                os.close(archive_lock)
            os.close(lock)

    def restore_generation(self, generation_id):
        """Read-only restoration: exact original member bytes and manifest.

        Returns ``{receipt, manifest, members, verification_level}``. It never
        creates a lock/directory, repairs a head, invokes an encoder or changes
        CURRENT/source evidence. Interrupted maintenance requires a writer.
        """
        from .archive_components import ACK_SCHEMA as V3_SCHEMA, ComponentArchive, LEVEL
        if self.archive_root is None:
            raise ValueError("RETENTION_ARCHIVE_DESTINATION_UNCONFIGURED")
        ident = _generation_id(generation_id)
        writer = os.open(self.root / "writer.lock", os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NOATIME", 0))
        archive_lock = None
        try:
            self._validate_read_lock(writer, self.root / "writer.lock")
            fcntl.flock(writer, fcntl.LOCK_SH | fcntl.LOCK_NB)
            archive_lock = os.open(self.archive_root / "archive.lock", os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NOATIME", 0))
            self._validate_read_lock(archive_lock, self.archive_root / "archive.lock")
            fcntl.flock(archive_lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            if (self.archive_root / "GC.json").exists():
                raise ValueError("RETENTION_ARCHIVE_GC_RECOVERY_REQUIRED")
            head = self._archive_checkpoint()
            chain = self._archive_receipts(head=head)
            receipt = next((row for row in chain if row["generation_id"] == ident), None)
            if receipt is None:
                raise ValueError("RETENTION_ARCHIVE_GENERATION_EXPIRED_OR_UNAVAILABLE")
            if receipt["receipt_sequence"] > head["receipt_count"]:
                raise ValueError("RETENTION_ARCHIVE_RECEIPT_UNCOMMITTED")
            if receipt["schema"] == V3_SCHEMA:
                members, manifest = ComponentArchive(self).restore(receipt)
                if receipt.get("source_as_of") != manifest.get("as_of"):
                    raise ValueError("RETENTION_ARCHIVE_MANIFEST_MISMATCH")
                return {"receipt": receipt, "manifest": manifest, "members": members, "verification_level": LEVEL}
            manifest = self._verify_archive(receipt)
            members = {}
            # A second open cannot borrow a preceding verification if a
            # non-cooperating actor rewrites/replaces the object between reads.
            # Bind this fresh immutable input to the original receipt SHA too.
            from .archive_components import read
            source, _ = read(self.archive_root / (ident + ".tar.gz"), maximum=self.archive_maximum_bytes)
            if hashlib.sha256(source).hexdigest() != receipt["archive_sha256"]:
                raise ValueError("RETENTION_ARCHIVE_OBJECT_HASH_MISMATCH")
            with io.BytesIO(source) as stream, gzip.GzipFile(fileobj=stream) as decoded:
                with tarfile.open(fileobj=decoded, mode="r|") as archive:
                    for member in archive:
                        members[member.name] = archive.extractfile(member).read(member.size)
            expected_names = {"report": "report.json.gz", "checkpoint": "checkpoint.json.gz", "status": "status.json"}
            if "projection.sqlite" in members:
                expected_names["projection"] = "projection.sqlite"
            if set(members) not in (BASE_MEMBERS, MEMBERS) or hashlib.sha256(members["manifest.json"]).hexdigest() != receipt["manifest_sha256"]:
                raise ValueError("RETENTION_ARCHIVE_MANIFEST_MISMATCH")
            if any(hashlib.sha256(members[name]).hexdigest() != manifest["files"][role]["sha256"]
                   for role, name in expected_names.items()):
                raise ValueError("RETENTION_ARCHIVE_MEMBER_HASH_MISMATCH")
            return {"receipt": receipt, "manifest": manifest, "members": members,
                    "verification_level": "LEGACY_V2_ALL_ORIGINAL_MEMBER_HASHES_AND_MANIFEST"}
        finally:
            if archive_lock is not None:
                os.close(archive_lock)
            os.close(writer)

    def _validate_read_lock(self, descriptor, path):
        info, target = os.fstat(descriptor), path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or self._same_file(info) != self._same_file(target)):
            raise ValueError("RETENTION_FILE_ALIAS_FORBIDDEN")

    def _read_only_paths(self, path):
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_NOATIME", 0))
        try:
            before = os.fstat(descriptor)
            names = os.listdir(descriptor)
            if len(names) > self.archive_maximum_files:
                raise ValueError("RETENTION_ARCHIVE_INVENTORY_LIMIT")
            if self._same_file(before) != self._same_file(os.fstat(descriptor)) or self._same_file(before) != self._same_file(path.lstat()):
                raise ValueError("RETENTION_ARCHIVE_NAMESPACE_CHANGED")
            return [path / name for name in names]
        finally:
            os.close(descriptor)

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
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NOATIME", 0))
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
            if self._same_file(os.fstat(fd)) != self._same_file(info) or self._same_file(path.lstat()) != self._same_file(info):
                raise ValueError("RETENTION_CONTROL_CHANGED")
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
            from .archive_components import ACK_SCHEMA as V3_SCHEMA
            accepted = (ack.get("schema") in {ARCHIVE_SCHEMA, V3_SCHEMA} and
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
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NOATIME", 0))
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                    info.st_size > (self.policy.maximum_bytes if maximum_bytes is None else maximum_bytes)):
                raise ValueError("RETENTION_ARCHIVE_MEMBER_INVALID")
            hasher = hashlib.sha256()
            while True:
                part = os.read(fd, 65536)
                if not part:
                    if self._same_file(os.fstat(fd)) != self._same_file(info) or self._same_file(path.lstat()) != self._same_file(info):
                        raise ValueError("RETENTION_ARCHIVE_MEMBER_CHANGED")
                    return hasher.hexdigest()
                hasher.update(part)
        finally:
            os.close(fd)

    @staticmethod
    def _same_file(info):
        return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid, info.st_nlink,
                info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_blocks)

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
            "entries_per_generation": len(MEMBERS) + 1,
            "minutes_to_hard_files": max(0, (self.policy.maximum_files - projected_files) / (len(MEMBERS) + 1) * .5),
            "file_horizon_assumption": "FOUR_PAYLOAD_ROLES_PLUS_MANIFEST_AND_DIRECTORY; NO_FURTHER_ARCHIVE_ROTATION",
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
