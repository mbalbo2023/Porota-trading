"""Owned native test fixtures on source's real filesystem, outside its custody."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

from rc6_audit_evidence.sqlite_scratch import SnapshotError, _storage_type


SOURCE_ROOT = Path(__file__).absolute().parents[1]


def external_disk_parent(parent=None):
    """Configured TMPDIR is strict; absent configuration uses an owned sibling."""
    if parent is None:
        configured = os.environ.get("TMPDIR")
        parent = Path(configured) if configured is not None else SOURCE_ROOT.parent
    parent = Path(parent)
    if (not parent.is_absolute() or ".." in parent.parts
            or any(path.is_symlink() for path in (parent, *parent.parents))
            or parent.is_relative_to(SOURCE_ROOT)):
        raise SnapshotError("EXTERNAL_DISK_FIXTURE_LITERAL_PARENT_REQUIRED")
    parent_info, source_info = parent.lstat(), SOURCE_ROOT.lstat()
    if (not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid != os.geteuid()
            or stat.S_IMODE(parent_info.st_mode) & 0o022):
        raise SnapshotError("EXTERNAL_DISK_FIXTURE_OWNED_PARENT_REQUIRED")
    if parent_info.st_dev != source_info.st_dev:
        raise SnapshotError("EXTERNAL_DISK_FIXTURE_SAME_FILESYSTEM_REQUIRED")
    _storage_type(parent)  # Original native disk policy, including volatile-FS denial.
    return parent


def make_external_disk_fixture(prefix, *, parent=None):
    if not isinstance(prefix, str) or re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", prefix) is None:
        raise SnapshotError("EXTERNAL_DISK_FIXTURE_SAFE_PREFIX_REQUIRED")
    parent = external_disk_parent(parent)
    root = Path(tempfile.mkdtemp(prefix=prefix, dir=parent))
    info = root.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700 or info.st_dev != SOURCE_ROOT.lstat().st_dev
            or root.is_symlink() or root.parent != parent or root.is_relative_to(SOURCE_ROOT)):
        raise SnapshotError("EXTERNAL_DISK_FIXTURE_PRIVATE_ROOT_REQUIRED")
    return root


@contextmanager
def external_disk_fixture(prefix, *, parent=None):
    root = make_external_disk_fixture(prefix, parent=parent)
    initial = root.lstat()
    identity = (initial.st_dev, initial.st_ino, initial.st_uid, initial.st_mode)
    try:
        yield root
    finally:
        current = root.lstat()
        if ((current.st_dev, current.st_ino, current.st_uid, current.st_mode) != identity
                or root.is_symlink()):
            raise SnapshotError("EXTERNAL_DISK_FIXTURE_CLEANUP_CUSTODY_CHANGED")
        shutil.rmtree(root)  # Only the fresh owned root, never its parent/source.
