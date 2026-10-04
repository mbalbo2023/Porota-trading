"""Quota admission and acknowledged rotation for private SHADOW evidence.

Call prepare only while EvidenceFiles owns its exclusive writer flock. Readers
hold a shared flock, so generation rotation cannot race a committed read. No
trading source, provider, broker, container, or external archive is accessed.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat

from rc6_dynamic_universe.common import digest, stamp


GENERATION = re.compile(r"gen-([0-9a-f]{32})\Z")
STAGING = re.compile(r"\.generation-([0-9a-f]{32})\.tmp\Z")
OWN_TEMP = re.compile(r"\.(?:CURRENT\.json|latest\.json\.gz|checkpoint\.json\.gz|status\.json|"
    r"preopen-\d{4}-\d{2}-\d{2}\.json\.gz)\.[a-z0-9_]{8}\.tmp\Z|"
    r"\.CURRENT\.[0-9a-f]{32}\.tmp\Z|\.independent-[0-9a-f]{32}\.tmp\Z")
MEMBERS = frozenset({"report.json.gz", "checkpoint.json.gz", "status.json", "manifest.json"})
HEX = re.compile(r"[0-9a-f]{64}\Z")
CONTROL_LIMIT = 256 * 1024


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
                 soft_ratio=.80, control_reserve_bytes=64 * 1024):
        self.policy = RetentionPolicy(maximum_bytes, maximum_files, soft_ratio, control_reserve_bytes)
        self.root = Path(root).absolute()
        # Resolving first would hide an alias in the root or an ancestor.
        if any(path.is_symlink() for path in (self.root, *self.root.parents)):
            raise ValueError("RETENTION_DIRECTORY_ALIAS_FORBIDDEN")
        if not self.root.is_dir():
            raise ValueError("RETENTION_DIRECTORY_REQUIRED")
        self.root = self.root.resolve()

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
            value = json.loads(raw)
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
            if not names <= MEMBERS or (complete and names != MEMBERS):
                raise ValueError("RETENTION_UNOWNED_TEMP_CONTENT")
            for name in names:
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError("RETENTION_FILE_ALIAS_FORBIDDEN")
            for name in sorted(names):
                os.unlink(name, dir_fd=fd)
            os.fsync(fd)
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
            accepted = (ack.get("schema") == "RC6_SHADOW_ARCHIVE_ACK_V1" and
                ack.get("generation_id") == generation_id and
                manifest.get("generation_id") == generation_id and
                ack.get("manifest_sha256") == manifest_hash and
                isinstance(ack.get("archive_sha256"), str) and
                HEX.fullmatch(ack["archive_sha256"]) is not None and
                ack["archive_sha256"] != "0" * 64 and
                isinstance(ack.get("archive_uri"), str) and 0 < len(ack["archive_uri"]) <= 2048 and
                ack.get("archive_verified") is True and ack.get("durable") is True and
                stamp(ack["acknowledged_at"]) <= datetime.now(timezone.utc))
            if not accepted or manifest.get("schema") != "rc6.shadow-evidence-generation.v1":
                return False
            files = manifest.get("files")
            if not isinstance(files, dict) or set(files) != {"report", "checkpoint", "status"}:
                return False
            expected_names = {"report": "report.json.gz", "checkpoint": "checkpoint.json.gz", "status": "status.json"}
            for role, expected_name in expected_names.items():
                member = files[role]
                if (not isinstance(member, dict) or member.get("name") != expected_name or
                        not isinstance(member.get("sha256"), str) or not HEX.fullmatch(member["sha256"]) or
                        self._file_hash(path / expected_name) != member["sha256"]):
                    return False
            return True
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def _file_hash(self, path):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                    info.st_size > self.policy.maximum_bytes):
                raise ValueError("RETENTION_ARCHIVE_MEMBER_INVALID")
            hasher = hashlib.sha256()
            while True:
                part = os.read(fd, 65536)
                if not part:
                    return hasher.hexdigest()
                hasher.update(part)
        finally:
            os.close(fd)

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
            return self._prepare(additional_bytes=additional_bytes, additional_files=additional_files, pinned=pinned)
        except OSError as exc:
            raise RetentionPressure("RETENTION_IO_UNAVAILABLE", {"error_class": type(exc).__name__}) from exc
        finally:
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
        except (OSError, ValueError) as exc:
            raise RetentionPressure("RETENTION_TEMP_CLEANUP_UNSAFE", {
                "files": len(entries), "bytes": size, "cleaned_temporaries": cleaned}) from exc
        if cleaned:
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
                if not match or match[1] in pins or not path.is_dir() or not self._acknowledged(path):
                    continue
                try:
                    self._remove_flat_directory(path, complete=True)
                    self._sync_root()
                except (OSError, ValueError) as exc:
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
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
