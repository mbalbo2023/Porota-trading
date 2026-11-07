"""One fail-closed, parent-anchored publisher for RC6 read-only source JSON.

Only callers with a declared output parent may publish.  This utility makes
no provider calls and never writes through a pre-existing temporary pathname.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import stat


class SourceOutputUnsafe(ValueError):
    """The owned destination or its directory identity is not established."""


_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_TEMP_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC


def _verified_directory(path: Path) -> int:
    """Open every existing ancestor from '/', rejecting all symlinks."""
    fd = os.open("/", _DIRECTORY_FLAGS)
    try:
        for part in path.parts[1:]:
            next_fd = os.open(part, _DIRECTORY_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _identity(directory_fd: int, name: str) -> tuple[int, ...] | None:
    try:
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise SourceOutputUnsafe("SOURCE_OUTPUT_UNSAFE_ALIAS")
    return (metadata.st_dev, metadata.st_ino, metadata.st_size,
            metadata.st_mtime_ns, metadata.st_ctime_ns)


def _same_parent(path: Path, owned_fd: int) -> bool:
    """Catch parent replacement, including an ancestor symlink after open."""
    check_fd = _verified_directory(path)
    try:
        left, right = os.fstat(owned_fd), os.fstat(check_fd)
        return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)
    finally:
        os.close(check_fd)


def publish_json(path: Path | str, payload: dict, *, allowed_parent: Path | str) -> None:
    """Atomically write JSON under exactly one existing, declared directory.

    O_NOFOLLOW protects every ancestor and O_EXCL creates unpredictable fresh
    staging.  os.replace with dirfds replaces a final symlink *itself*, never
    follows it; adversarial same-UID directory renames remain a host-ownership
    question and are not represented as physically certified here.
    """
    destination, parent = Path(path), Path(allowed_parent)
    if (not destination.is_absolute() or not parent.is_absolute()
            or '..' in destination.parts or '..' in parent.parts
            or destination.parent != parent or destination.name in ('', '.', '..')):
        raise SourceOutputUnsafe("SOURCE_OUTPUT_UNSAFE_PARENT")
    directory_fd = None
    staged = None
    try:
        # The owner must have provisioned this directory; never mkdir an
        # unverified path that might belong to another application.
        directory_fd = _verified_directory(parent)
        before = _identity(directory_fd, destination.name)
        staged = "." + destination.name + "." + secrets.token_hex(16) + ".tmp"
        staging_fd = os.open(staged, _TEMP_FLAGS, 0o600, dir_fd=directory_fd)
        with os.fdopen(staging_fd, "w", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, sort_keys=True)
            output.flush()
            os.fchmod(output.fileno(), 0o644)
            os.fsync(output.fileno())
            staged_identity = os.fstat(output.fileno())
        if (not _same_parent(parent, directory_fd)
                or _identity(directory_fd, destination.name) != before):
            raise SourceOutputUnsafe("SOURCE_OUTPUT_UNSAFE_RACE")
        os.replace(staged, destination.name,
                   src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        staged = None
        os.fsync(directory_fd)
        actual = _identity(directory_fd, destination.name)
        if (not _same_parent(parent, directory_fd) or actual is None
                or actual[:2] != (staged_identity.st_dev, staged_identity.st_ino)):
            raise SourceOutputUnsafe("SOURCE_OUTPUT_UNSAFE_POSTREPLACE")
    except OSError as exc:
        raise SourceOutputUnsafe("SOURCE_OUTPUT_UNSAFE_IO") from exc
    finally:
        if staged is not None and directory_fd is not None:
            try:
                os.unlink(staged, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        if directory_fd is not None:
            os.close(directory_fd)
