"""Read-only, stdlib-only admission for the private archive namespace.

This inventories custody metadata and physical residence. It does not decode
archive objects, verify receipts, recover temporary files, or create a lock.
The source can be embedded verbatim in a pretransfer probe.
"""
import errno
import fcntl
import os
from pathlib import Path
import re
import stat


SCHEMA = "rc6.shadow-archive-namespace-admission.v1"
LEVEL = "BOUNDED_ARCHIVE_NAMESPACE_AND_CUSTODY_METADATA"
DEFAULT_MAXIMUM_BYTES = 512 * 1024**2
DEFAULT_MAXIMUM_FILES = 32768
_MEMBER = re.compile(r"(?:[0-9a-f]{32}\.(?:tar\.gz|recipe\.gz|receipt\.json)|"
    r"[0-9a-f]{64}\.cas\.pack|CHECKPOINT\.json|GC\.json|BUILD\.json|archive\.lock)\Z")
_TEMPORARY = re.compile(r"\.(?:archive|control|cas|recipe|gc)-[0-9a-f]{32}\.tmp\Z")


def is_archive_member(name):
    return isinstance(name, str) and _MEMBER.fullmatch(name) is not None


def is_archive_temporary(name):
    return isinstance(name, str) and _TEMPORARY.fullmatch(name) is not None


def _custody(info, *, owner_uid, directory=False):
    expected_mode = 0o700 if directory else 0o600
    if (not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or info.st_uid != owner_uid or stat.S_IMODE(info.st_mode) != expected_mode
            or (not directory and info.st_nlink != 1)):
        raise ValueError("ARCHIVE_CUSTODY_INVALID")
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            info.st_blocks)


def _read_only_open(name, *, directory=False, dir_fd=None):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME
    if directory:
        flags |= os.O_DIRECTORY
    try:
        return os.open(name, flags, dir_fd=dir_fd)
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.ENOTDIR):
            raise ValueError("ARCHIVE_ALIAS_FORBIDDEN") from error
        if error.errno == errno.ENOENT:
            raise ValueError("ARCHIVE_ROOT_REQUIRED") from error
        raise ValueError("ARCHIVE_CUSTODY_UNAVAILABLE") from error


def inspect_archive(root, *, max_bytes=DEFAULT_MAXIMUM_BYTES, owner_uid=1000,
                    maximum_files=DEFAULT_MAXIMUM_FILES):
    """Return bounded physical occupancy without changing source metadata.

    An empty prepared root is valid before its first writer creates a lock.
    A nonempty root requires the existing archive lock. Temporary objects are
    counted and reported for writer recovery, never deleted by this inspector.
    Unknown names, aliases, wrong custody, concurrent mutation and exhausted
    byte or inode capacity fail closed.
    """
    if (type(max_bytes) is not int or not 0 < max_bytes <= DEFAULT_MAXIMUM_BYTES
            or type(maximum_files) is not int or not 0 < maximum_files <= DEFAULT_MAXIMUM_FILES
            or type(owner_uid) is not int or owner_uid < 0):
        raise ValueError("ARCHIVE_ADMISSION_POLICY_INVALID")
    path = Path(root).absolute()
    try:
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise ValueError("ARCHIVE_ALIAS_FORBIDDEN")
        descriptor = _read_only_open(path, directory=True)
        lock = None
        try:
            before = _custody(os.fstat(descriptor), owner_uid=owner_uid, directory=True)
            if _custody(path.lstat(), owner_uid=owner_uid, directory=True) != before:
                raise ValueError("ARCHIVE_NAMESPACE_CHANGED")
            try:
                lock_info = os.stat("archive.lock", dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                lock_info = None
            if lock_info is not None:
                expected = _custody(lock_info, owner_uid=owner_uid)
                lock = _read_only_open("archive.lock", dir_fd=descriptor)
                if _custody(os.fstat(lock), owner_uid=owner_uid) != expected:
                    raise ValueError("ARCHIVE_NAMESPACE_CHANGED")
                try:
                    fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise ValueError("ARCHIVE_WRITER_ACTIVE") from error
            members, occupied, allocated, temporary = {}, 0, 0, 0
            with os.scandir(descriptor) as iterator:
                for entry in iterator:
                    if len(members) >= maximum_files:
                        raise ValueError("ARCHIVE_FILES_CAPACITY_REACHED")
                    is_temporary = is_archive_temporary(entry.name)
                    if not is_temporary and not is_archive_member(entry.name):
                        raise ValueError("ARCHIVE_NAMESPACE_UNKNOWN")
                    proof = _custody(entry.stat(follow_symlinks=False), owner_uid=owner_uid)
                    members[entry.name] = proof
                    occupied += proof[6]
                    allocated += proof[9] * 512
                    temporary += int(is_temporary)
                    if occupied >= max_bytes:
                        raise ValueError("ARCHIVE_BYTES_CAPACITY_REACHED")
                    if allocated + before[9]*512 >= max_bytes:
                        raise ValueError("ARCHIVE_ALLOCATED_BYTES_CAPACITY_REACHED")
            if members and lock is None:
                raise ValueError("ARCHIVE_LOCK_REQUIRED")
            if len(members) >= maximum_files:
                raise ValueError("ARCHIVE_FILES_CAPACITY_REACHED")
            for name, expected in members.items():
                if _custody(os.stat(name, dir_fd=descriptor, follow_symlinks=False), owner_uid=owner_uid) != expected:
                    raise ValueError("ARCHIVE_NAMESPACE_CHANGED")
            if (_custody(os.fstat(descriptor), owner_uid=owner_uid, directory=True) != before
                    or _custody(path.lstat(), owner_uid=owner_uid, directory=True) != before):
                raise ValueError("ARCHIVE_NAMESPACE_CHANGED")
            available = os.fstatvfs(descriptor)
            return {"schema": SCHEMA, "verification_level": LEVEL,
                "state": "RECOVERY_REQUIRED" if temporary or "BUILD.json" in members or "GC.json" in members else "WITHIN_QUOTA",
                "occupied_bytes": occupied, "files": len(members), "inodes": len(members) + 1,
                "allocated_bytes": allocated, "allocated_directory_bytes": before[9] * 512,
                "growth_remaining_bytes": max_bytes - occupied, "max_bytes": max_bytes,
                "maximum_files": maximum_files, "owner_uid": owner_uid,
                "free_bytes": available.f_bavail * available.f_frsize,
                "owned_temporary_count": temporary}
        finally:
            if lock is not None:
                os.close(lock)
            os.close(descriptor)
    except ValueError:
        raise
    except OSError as error:
        raise ValueError("ARCHIVE_NAMESPACE_UNAVAILABLE") from error
