"""Private, budgeted disk scratch for captured SQLite images, never authority.

The runtime root belongs to the primary PAPER dataset, even when the captured
source is a separate history database. A lease serializes readers; identified
residue is retained and counted after a killed reader, rather than deleting it.
"""
from __future__ import annotations

from contextlib import contextmanager
import errno
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import threading


MAX_BYTES = 512 * 1024 * 1024
RESERVE_BYTES = 2 * 1024 * 1024 * 1024
MIN_FREE_INODE_PERCENT = 10
ENV_PREFIX = "POROTA_SQLITE_SCRATCH_"
LOCK = ".rc6-sqlite-scratch.lock"
MARKER = ".rc6-snapshot.json"
PREFIX = "rc6-sqlite-copy-"
SCHEMA = "RC6_SQLITE_DERIVED_SCRATCH_V1"
MEMBERS = {MARKER, "snapshot.sqlite", "snapshot.sqlite-wal", "snapshot.sqlite-shm",
           "snapshot.sqlite-journal"}
MAX_SESSIONS = 64


class SnapshotError(ValueError):
    """Sanitized failure reason, without private paths or SQLite details."""


def snapshot_peak_bytes(main_bytes, wal_bytes=0, source_shm_bytes=0, *, allocation_unit=4096):
    """Conservative total for one capture, including reconstructed WAL index.

    WAL frames are bounded with SQLite's smallest page (512 + 24 byte header).
    A 32KiB index region per 4,000 frames overestimates SQLite's 4,062/4,096
    frame regions. SHM bytes from source are verified, not transported. Metadata
    includes four allocation units plus 1MiB of filesystem/SQLite margin.
    Existing scratch residue must be added separately to this pure estimate.
    """
    values = (main_bytes, wal_bytes, source_shm_bytes, allocation_unit)
    if any(type(value) is not int or value < 0 for value in values) or allocation_unit == 0:
        raise SnapshotError("BOUNDED_SCRATCH_BUDGET_REQUIRED")
    rounded = lambda value: ((value + allocation_unit - 1)//allocation_unit)*allocation_unit
    frames = (max(wal_bytes - 32, 0) + 535)//536
    reconstructed = 32768 * max(1, (frames + 3999)//4000) if wal_bytes else 0
    shm = max(source_shm_bytes, reconstructed)
    return sum(rounded(value) for value in (main_bytes, wal_bytes, shm)) + 1048576 + 4*allocation_unit


def _bounded_integer(value, *, minimum, maximum=None):
    if isinstance(value, str):
        if not re.fullmatch(r"[0-9]{1,20}", value):
            raise SnapshotError("BOUNDED_SCRATCH_BUDGET_REQUIRED")
        value = int(value)
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise SnapshotError("BOUNDED_SCRATCH_BUDGET_REQUIRED")
    return value


def _configuration(scratch_root, max_scratch_bytes, reserve_bytes, min_free_inode_percent):
    env = os.environ
    configured = any(key.startswith(ENV_PREFIX) for key in env)
    limits = (max_scratch_bytes, reserve_bytes, min_free_inode_percent)
    if configured:
        names = ("ROOT", "MAX_BYTES", "RESERVE_BYTES", "MIN_FREE_INODE_PERCENT")
        if any(not env.get(ENV_PREFIX + name, "") for name in names):
            raise SnapshotError("SCRATCH_RUNTIME_CONFIGURATION_REQUIRED")
        try:
            from cg_paper_workspace import artifact_root, database_path
            expected = artifact_root(database_path()) / "sqlite-read-scratch"
        except (TypeError, ValueError, OSError):
            raise SnapshotError("SCRATCH_CANONICAL_ROOT_REQUIRED") from None
        configured_root = Path(env[ENV_PREFIX + "ROOT"]).absolute()
        if configured_root != expected or (scratch_root is not None and Path(scratch_root).absolute() != expected):
            raise SnapshotError("SCRATCH_CANONICAL_ROOT_REQUIRED")
        scratch_root = configured_root
        base = (
            _bounded_integer(env[ENV_PREFIX + "MAX_BYTES"], minimum=1, maximum=MAX_BYTES),
            _bounded_integer(env[ENV_PREFIX + "RESERVE_BYTES"], minimum=RESERVE_BYTES),
            _bounded_integer(env[ENV_PREFIX + "MIN_FREE_INODE_PERCENT"], minimum=MIN_FREE_INODE_PERCENT, maximum=99),
        )
        limits = tuple(base[i] if value is None else value for i, value in enumerate(limits))
        limits = (
            _bounded_integer(limits[0], minimum=1, maximum=MAX_BYTES),
            _bounded_integer(limits[1], minimum=0),
            _bounded_integer(limits[2], minimum=0, maximum=99),
        )
        if limits[0] > base[0] or limits[1] < base[1] or limits[2] < base[2]:
            raise SnapshotError("SCRATCH_RUNTIME_BUDGET_WEAKENING_FORBIDDEN")
    elif scratch_root is None:
        if any(value is not None for value in limits):
            raise SnapshotError("SCRATCH_ROOT_REQUIRED")
        return None
    else:
        limits = tuple(default if value is None else value for default, value in
                       zip((MAX_BYTES, RESERVE_BYTES, MIN_FREE_INODE_PERCENT), limits))
    limits = (
        _bounded_integer(limits[0], minimum=1, maximum=MAX_BYTES),
        _bounded_integer(limits[1], minimum=0),
        _bounded_integer(limits[2], minimum=0, maximum=99),
    )
    return Path(scratch_root).absolute(), limits


def _storage_type(path):
    """Read the owning Linux mount; opaque or volatile scratch fails closed."""
    try:
        candidates = []
        with open("/proc/self/mountinfo", encoding="utf-8") as mounts:
            for line in mounts:
                fields = line.split()
                separator = fields.index("-")
                mount = Path(re.sub(r"\\([0-7]{3})", lambda found: chr(int(found[1], 8)), fields[4]))
                if path == mount or mount in path.parents:
                    candidates.append((len(mount.parts), fields[separator + 1]))
        filesystem = max(candidates)[1]
    except (OSError, ValueError, IndexError):
        raise SnapshotError("SCRATCH_FILESYSTEM_UNVERIFIED") from None
    if filesystem in {"tmpfs", "ramfs", "devtmpfs", "proc", "sysfs", "cgroup", "cgroup2"}:
        raise SnapshotError("SCRATCH_DISK_BACKED_REQUIRED")
    return filesystem


def _identity(info):
    return info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_nlink


def _private(info, *, directory=False, readonly_image=False):
    expected_mode = 0o700 if directory else 0o600
    allowed_modes = {expected_mode, 0o400} if readonly_image and not directory else {expected_mode}
    if (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) not in allowed_modes
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or (not directory and info.st_nlink != 1)):
        raise SnapshotError("SCRATCH_CUSTODY_UNVERIFIED")


def _occupation(info):
    return max(info.st_size, info.st_blocks * 512)


class _Lease:
    def __init__(self, root, limits, peak):
        self.root, self.limits, self.peak = root, limits, peak
        self.root_fd = self.lock_fd = None
        self.session = None
        self._thread, self._pid = threading.get_ident(), os.getpid()
        self._acquired = False
        self._parent = None
        self._root_owner = self
        self._active_leases = set()

    def acquire(self):
        if self.root.resolve() != self.root or any(parent.is_symlink() for parent in self.root.parents):
            raise SnapshotError("SCRATCH_CUSTODY_UNVERIFIED")
        self.filesystem = _storage_type(self.root)
        self.root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        root_info = os.fstat(self.root_fd)
        _private(root_info, directory=True)
        # A session directory legitimately changes its parent's link count.
        self.root_identity = _identity(root_info)[:4]
        self.lock_fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=self.root_fd)
        lock_info = os.fstat(self.lock_fd)
        _private(lock_info)
        self.lock_identity = _identity(lock_info)
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN}:
                raise SnapshotError("SCRATCH_LEASE_BUSY") from None
            raise
        self._acquired = True
        self._active_leases.add(self)
        occupied = self.measure()
        if occupied + self.peak > self.limits[0]:
            raise SnapshotError("SCRATCH_BYTE_BUDGET_EXHAUSTED")
        self.capacity(additional_bytes=self.peak, additional_inodes=7)
        self.admission = {"lease": "EXCLUSIVE_OWNER", "occupied_bytes": occupied,
            "additional_peak_bytes": self.peak, "estimated_total_peak_bytes": occupied+self.peak}

    def borrow(self, owner):
        """Bind a sibling namespace to the same held lock, never a new owner.

        The caller supplies the actual live capture lease, not a receipt/cache.
        Duplicated FDs retain the same Linux open-file-description flock until
        the owning capture and every authenticated sibling are closed.
        """
        if type(owner) is not _Lease:
            raise SnapshotError("SCRATCH_OWNER_LEASE_REQUIRED")
        owner.verify()
        if (self.root != owner.root or self.limits != owner.limits
                or threading.get_ident() != owner._thread or os.getpid() != owner._pid):
            raise SnapshotError("SCRATCH_OWNER_LEASE_REQUIRED")
        self._parent, self._root_owner = owner, owner._root_owner
        self.root_fd, self.lock_fd = os.dup(owner.root_fd), os.dup(owner.lock_fd)
        self.root_identity, self.lock_identity = owner.root_identity, owner.lock_identity
        self.filesystem = owner.filesystem
        self._acquired = True
        occupied = self.measure()
        # Count actual residue and reserve every live image's unmaterialized
        # peak (e.g. regenerated SHM), rather than approving each in isolation.
        remaining_bytes = remaining_inodes = 0
        for active in self._root_owner._active_leases:
            active.verify()
            used_bytes, used_inodes = active._session_occupation()
            remaining_bytes += max(0, active.peak-used_bytes)
            remaining_inodes += max(0, 7-used_inodes)
        estimated = occupied+remaining_bytes+self.peak
        if estimated > self.limits[0]:
            raise SnapshotError("SCRATCH_BYTE_BUDGET_EXHAUSTED")
        self.capacity(additional_bytes=remaining_bytes+self.peak,
                      additional_inodes=remaining_inodes+7)
        self._root_owner._active_leases.add(self)
        self.admission = {"lease": "BORROWED_AUTHENTICATED_OWNER", "occupied_bytes": occupied,
            "live_unmaterialized_peak_bytes": remaining_bytes,
            "additional_peak_bytes": self.peak, "estimated_total_peak_bytes": estimated}

    def _session_occupation(self):
        if self.session is None:
            return 0, 0
        info = self.session.lstat()
        if _identity(info) != self.session_identity:
            raise SnapshotError("SCRATCH_CUSTODY_UNVERIFIED")
        total, inodes = _occupation(info), 1
        for member in self.session.iterdir():
            item = member.lstat()
            if member.name not in MEMBERS:
                raise SnapshotError("SCRATCH_CUSTODY_UNVERIFIED")
            _private(item, readonly_image=member.name in {"snapshot.sqlite", "snapshot.sqlite-wal"})
            total += _occupation(item)
            inodes += 1
        return total, inodes

    def verify(self):
        if (not self._acquired or threading.get_ident() != self._thread or os.getpid() != self._pid):
            raise SnapshotError("SCRATCH_OWNER_LEASE_REQUIRED")
        if self._parent is not None:
            self._parent.verify()
        if (_identity(os.stat(self.root, follow_symlinks=False))[:4] != self.root_identity
                or _identity(os.stat(LOCK, dir_fd=self.root_fd, follow_symlinks=False)) != self.lock_identity):
            raise SnapshotError("SCRATCH_CUSTODY_UNVERIFIED")

    def measure(self):
        self.verify()
        total = _occupation(os.fstat(self.root_fd))
        with os.scandir(self.root_fd) as entries:
            names = []
            for entry in entries:
                names.append(entry.name)
                if len(names) > MAX_SESSIONS + 1:
                    raise SnapshotError("SCRATCH_INVENTORY_BUDGET_EXHAUSTED")
        self.residual_sessions = sum(name != LOCK for name in names)
        for name in names:
            info = os.stat(name, dir_fd=self.root_fd, follow_symlinks=False)
            total += _occupation(info)
            if name == LOCK:
                _private(info)
                continue
            if not name.startswith(PREFIX):
                raise SnapshotError("SCRATCH_CUSTODY_UNVERIFIED")
            _private(info, directory=True)
            descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.root_fd)
            try:
                if _identity(os.fstat(descriptor)) != _identity(info):
                    raise SnapshotError("SCRATCH_CUSTODY_UNVERIFIED")
                with os.scandir(descriptor) as members:
                    files = []
                    for member in members:
                        if member.name not in MEMBERS or len(files) >= len(MEMBERS):
                            raise SnapshotError("SCRATCH_CUSTODY_UNVERIFIED")
                        files.append(member.name)
                if MARKER not in files:
                    raise SnapshotError("SCRATCH_CUSTODY_UNVERIFIED")
                for member in files:
                    item = os.stat(member, dir_fd=descriptor, follow_symlinks=False)
                    _private(item, readonly_image=member in {"snapshot.sqlite", "snapshot.sqlite-wal"})
                    total += _occupation(item)
                marker_fd = os.open(MARKER, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor)
                with os.fdopen(marker_fd, "rb") as marker_stream:
                    if os.fstat(marker_stream.fileno()).st_size > 4096:
                        raise SnapshotError("SCRATCH_CUSTODY_UNVERIFIED")
                    try:
                        marker = json.loads(marker_stream.read(4097))
                    except (ValueError, UnicodeError):
                        raise SnapshotError("SCRATCH_CUSTODY_UNVERIFIED") from None
                if (not isinstance(marker, dict) or marker.get("schema") != SCHEMA
                        or marker.get("owner_uid") != os.geteuid() or marker.get("session") != name
                        or type(marker.get("estimated_peak_bytes")) is not int
                        or not 0 < marker["estimated_peak_bytes"] <= MAX_BYTES
                        or type(marker.get("pid")) is not int or marker["pid"] <= 0
                        or not re.fullmatch(r"[0-9a-f]{64}", str(marker.get("source_identity_sha256", "")))):
                    raise SnapshotError("SCRATCH_CUSTODY_UNVERIFIED")
            finally:
                os.close(descriptor)
        return total

    def capacity(self, *, additional_bytes=0, additional_inodes=0):
        filesystem = os.fstatvfs(self.root_fd)
        if filesystem.f_bavail * filesystem.f_frsize < self.limits[1] + additional_bytes:
            raise SnapshotError("SCRATCH_FREE_SPACE_RESERVE_REQUIRED")
        if filesystem.f_files <= 0 or filesystem.f_favail < additional_inodes + math.ceil(filesystem.f_files * self.limits[2]/100):
            raise SnapshotError("SCRATCH_FREE_INODES_RESERVE_REQUIRED")

    def check(self):
        if self.measure() > self.limits[0]:
            raise SnapshotError("SCRATCH_BYTE_BUDGET_EXHAUSTED")
        self.capacity()

    def create(self, source):
        self.session = Path(tempfile.mkdtemp(prefix=PREFIX, dir=self.root))
        self.session_identity = _identity(self.session.stat())
        marker = {"schema": SCHEMA, "owner_uid": os.geteuid(), "pid": os.getpid(),
                  "session": self.session.name, "estimated_peak_bytes": self.peak,
                  "source_identity_sha256": hashlib.sha256(os.fsencode(source)).hexdigest()}
        marker_fd = os.open(self.session / MARKER, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(marker_fd, "w", encoding="utf-8") as stream:
            json.dump(marker, stream, sort_keys=True, separators=(",", ":"))
        self.marker_bytes = json.dumps(marker,sort_keys=True,separators=(",", ":")).encode()
        self.marker_identity = _identity((self.session / MARKER).stat())
        self.check()
        return self.session

    def cleanup(self):
        if self.session is None:
            return
        self.verify()
        info = os.stat(self.session.name, dir_fd=self.root_fd, follow_symlinks=False)
        if _identity(info) != self.session_identity:
            raise SnapshotError("SCRATCH_CUSTODY_UNVERIFIED")
        descriptor = os.open(self.session.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.root_fd)
        try:
            with os.scandir(descriptor) as entries:
                names = []
                for entry in entries:
                    if entry.name not in MEMBERS or len(names) >= len(MEMBERS):
                        raise SnapshotError("SCRATCH_CUSTODY_UNVERIFIED")
                    _private(entry.stat(follow_symlinks=False),
                             readonly_image=entry.name in {"snapshot.sqlite", "snapshot.sqlite-wal"})
                    names.append(entry.name)
            if MARKER not in names or not hasattr(self,'marker_identity'):
                raise SnapshotError('SCRATCH_CUSTODY_UNVERIFIED')
            marker_fd = os.open(MARKER,os.O_RDONLY | os.O_NOFOLLOW,dir_fd=descriptor)
            with os.fdopen(marker_fd,'rb') as marker:
                if (_identity(os.fstat(marker.fileno()))!=self.marker_identity
                        or marker.read(4097)!=self.marker_bytes):
                    raise SnapshotError('SCRATCH_CUSTODY_UNVERIFIED')
            # Validate the complete inventory before deleting any own member.
            for name in names:
                os.unlink(name, dir_fd=descriptor)
        finally:
            os.close(descriptor)
        os.rmdir(self.session.name, dir_fd=self.root_fd)

    def close(self):
        self._acquired = False
        self._root_owner._active_leases.discard(self)
        if self.lock_fd is not None:
            os.close(self.lock_fd)
            self.lock_fd = None
        if self.root_fd is not None:
            os.close(self.root_fd)
            self.root_fd = None


def inspect_scratch(root, *, max_bytes=MAX_BYTES, reserve_bytes=RESERVE_BYTES,
                    min_free_inode_percent=MIN_FREE_INODE_PERCENT):
    """Inspect existing private disk scratch without SQLite or deleting files.

    Run as the runtime owner (bot1000); this may create its empty lease file.
    Busy readers fail closed. The returned occupation includes retained,
    identified residue. A deploy capacity guard can reserve future growth as
    ``max(0, configured_max_bytes - occupied_bytes)`` without double counting.
    The root is initialized separately; no parent directories are created here.
    This function uses only the standard library, including in a remote probe.
    """
    limits = (_bounded_integer(max_bytes, minimum=1, maximum=MAX_BYTES),
              _bounded_integer(reserve_bytes, minimum=0),
              _bounded_integer(min_free_inode_percent, minimum=0, maximum=99))
    lease = _Lease(Path(root).absolute(), limits, 0)
    try:
        lease.acquire()
        occupied = lease.measure()
        available = os.fstatvfs(lease.root_fd)
        return {"filesystem": lease.filesystem, "occupied_bytes": occupied,
                "residual_sessions": lease.residual_sessions, "owner_uid": os.geteuid(),
                "mode": "0700", "allocation_unit": available.f_frsize,
                "free_bytes": available.f_bavail*available.f_frsize,
                "total_inodes": available.f_files, "free_inodes": available.f_favail,
                "max_bytes": limits[0], "reserve_bytes": limits[1],
                "min_free_inode_percent": limits[2]}
    except OSError:
        raise SnapshotError("SCRATCH_UNAVAILABLE") from None
    finally:
        lease.close()


@contextmanager
def private_scratch(source, *, main_bytes, wal_bytes, source_shm_bytes, scratch_root=None,
                    max_scratch_bytes=None, reserve_bytes=None, min_free_inode_percent=None,
                    owner_lease=None):
    configuration = _configuration(scratch_root, max_scratch_bytes, reserve_bytes, min_free_inode_percent)
    if configuration is None:
        if owner_lease is not None:
            raise SnapshotError("SCRATCH_OWNER_LEASE_REQUIRED")
        with tempfile.TemporaryDirectory(prefix=PREFIX) as scratch:
            yield Path(scratch), None
        return
    root, limits = configuration
    if source == root or root in source.parents or source in root.parents:
        raise SnapshotError("SCRATCH_SOURCE_OVERLAP_FORBIDDEN")
    lease = None
    try:
        # Preflight a private descriptor before calculating allocation-rounded
        # requirements. No directory or parent is silently created by readers.
        peak = snapshot_peak_bytes(main_bytes, wal_bytes, source_shm_bytes,
                                   allocation_unit=os.statvfs(root).f_frsize)
        lease = _Lease(root, limits, peak)
        if owner_lease is None:
            lease.acquire()
        else:
            lease.borrow(owner_lease)
        try:
            scratch = lease.create(source)
            yield scratch, lease
        finally:
            lease.cleanup()
    except OSError:
        raise SnapshotError("SCRATCH_UNAVAILABLE") from None
    finally:
        if lease is not None:
            lease.close()
