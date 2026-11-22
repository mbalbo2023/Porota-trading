#!/usr/bin/env python3
"""RC6 OBSERVER one-shot online custody. Never run from an untrusted ref."""

from __future__ import annotations

from contextlib import ExitStack, closing, contextmanager
import base64
from datetime import date, datetime, timedelta, timezone
import errno
import fcntl
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request


SCHEMA = "rc6.observer-online-custody-once.v1"
ROOT = Path("/opt/porota-trading/data")
OBSERVER = ROOT / "paper_v17/observer_v17.db"
HISTORY = ROOT / "market_history.db"
DATABASES = {"OBSERVER": OBSERVER, "HISTORY": HISTORY}
MARKER = ROOT / "deploy/CURRENT_STATE_V2.json"
# A UID1000-private host path OUTSIDE both Docker role bind mount allowlists.
# Source SQLite/WAL/SHM are opened AS UID1000, never as privileged root.
CUSTODY = Path("/home/porotaadmin/.local/share/rc6-observer-custody")
EXPECTED_SOURCE_SHA = "615ed9056e672074a4ea01ddc7ff800409a2f9ca"
EXPECTED_TREE_SHA = "54618bc017cfea0167c3e94e7a460631ad4543b5"
EXPECTED_IMAGE_ID = "sha256:384282ef540e0e7133f5d0449f02a2953306a318a4f673e7817ed06fb0bddd4b"
CUSTODY_RELEASE = (EXPECTED_SOURCE_SHA, EXPECTED_TREE_SHA, EXPECTED_IMAGE_ID)
HISTORY_MODES = {"HISTORY_REFERENCE_PREPARE", "HISTORY_REFERENCE_APPLY"}
HISTORY_VERIFY = "HISTORY_REFERENCE_VERIFY"
HISTORY_WRITE_SCOPE = "RC6_HISTORY_REFERENCE_IMPORT_ONCE"
HISTORY_LEGACY_ROWS = 596196
HISTORY_LEGACY_CANONICAL = 439833
CAPSULE_MAX = 16 * 1024**2
CAPSULE_PART = 512 * 1024
CAPTURE_PINS = {
    "cutoff.zip": ("b420f63c42f7ac4ceb791ec9bc521c281cb08a1a8e62f094d4bf46c421f38ff2", 151970),
    "complement.zip": ("4a4aabbe0633c60b13238691f4f3abf3bec1fee492bbdf6ae4eee53bd3944756", 82),
}
MAX_DB_BYTES = 5 * 1024**3
MAX_WAL_BYTES = 512 * 1024**2
MIN_RESERVE_BYTES = 2 * 1024**3
RUN_ID = re.compile(r"[1-9][0-9]{7,14}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
OWNER_BRANCH = "mbalbo2023/Porota-trading"
OWNER_ISSUE = f"https://api.github.com/repos/{OWNER_BRANCH}/issues/980"
OWNER_PUBLIC = f"https://github.com/{OWNER_BRANCH}/issues/980#issuecomment-"
BACKUP_TIMEOUT_SECONDS = 600
INVENTORY_MODE = "RESTORED_HISTORY_READ_ONLY"
INVENTORY_SCHEMA = "rc6.restored-history-inventory.v1"
MIGRATION_SCHEMA = "RC6_OFFHOST_MIGRATION_REHEARSAL_V1"
MIGRATION_MAX_BYTES = 2 * 1024**3
INVENTORY_FROM = date(2025, 9, 21)
INVENTORY_TO = date(2026, 9, 21)
INVENTORY_LENGTH = (INVENTORY_TO - INVENTORY_FROM).days + 1
INVENTORY_FAMILIES = ("ACCIONES", "CEDEARS")
INVENTORY_IDENTITY_LIMIT = 4096
INVENTORY_METRICS = ("identities", "with_canonical", "regular_or_certified_slots",
                     "canonical_on_certified_slots", "absent_on_certified_slots",
                     "canonical_on_uncertain_slots", "canonical_outside_market_days",
                     "version_rows", "conflicted_candidate_slots", "invalid_date_rows",
                     "canonical_without_exact_version", "legacy_candidates_on_certified_slots")
INVENTORY_JOIN_FIELDS = ("symbol", "instrument_type", "market", "settlement", "date")
INVENTORY_EXACT_FIELDS = frozenset((*INVENTORY_JOIN_FIELDS, "currency", "price_basis",
                                    "adjustment_basis", "adjusted"))
INVENTORY_TABLE_FIELDS = {
    "history_canonical_v2": INVENTORY_EXACT_FIELDS | {"version_id"},
    "history_versions_v2": INVENTORY_EXACT_FIELDS | {"id"},
}
INVENTORY_EXACT_METRICS = frozenset(INVENTORY_METRICS) - {
    "identities", "regular_or_certified_slots", "legacy_candidates_on_certified_slots"}
INVENTORY_SQL_PHASES = frozenset({"OPEN_RESTORE", "OBSERVER_PAPER_WITNESS", "OBSERVER_CATALOG",
                                  "HISTORY_SCHEMA", "HISTORY_CANONICAL", "HISTORY_VERSIONS"})
PUBLIC_HOLD_REASONS = frozenset({
    "CUSTODY_BACKUP_TIMEOUT", "CUSTODY_DIRECTORY_UNSAFE", "CUSTODY_DISK_RESERVE_BLOCKED",
    "CUSTODY_DURABILITY_UNVERIFIED", "CUSTODY_LOCK_UNSAFE", "CUSTODY_OWNER_BUSY",
    "CUSTODY_RUN_ALREADY_EXISTS", "CUSTODY_SQLITE_BACKUP_TIMEOUT",
    "CUSTODY_SQLITE_UNAVAILABLE", "CUSTODY_UNCLASSIFIED",
    "EXIT_QUIESCENCE_UNVERIFIED", "PAPER_ORDERS_UNVERIFIED",
    "PARENT_PATH_UNAVAILABLE", "PARENT_PATH_UNSAFE", "PRIVILEGED_PATH_BINDING_UNVERIFIED",
    "PRIVATE_HISTORY_RESTORE_SCHEMA_UNVERIFIED", "PRIVATE_RESTORE_DIGEST_MISMATCH",
    "PRIVATE_RESTORE_QUICK_CHECK_FAILED", "RELEASE_PAPER_IDENTITY_UNVERIFIED",
    "ROOTLESS_SSH_IDENTITY_REQUIRED", "RUNTIME_DB_BINDING_NO_VERIFICADO",
    "ROOTLESS_STATE_PARENT_UNSAFE", "ROOTLESS_STATE_PRIVATE_GROUP_UNVERIFIED",
    "SOURCE_FILE_UNAVAILABLE", "SOURCE_FILE_UNSAFE", "SOURCE_IDENTITY_CHANGED",
    "SQLITE_SIDECAR_UNSAFE",
    "RESTORED_INVENTORY_BINDING_UNVERIFIED", "RESTORED_INVENTORY_DIGEST_MISMATCH",
    "RESTORED_INVENTORY_SQL_UNVERIFIED", "RESTORED_INVENTORY_SCOPE_UNVERIFIED",
    "RESTORED_INVENTORY_CALENDAR_UNVERIFIED", "RESTORED_INVENTORY_TIMEOUT",
})


class Hold(Exception):
    """Fail-closed expected condition; never publish the triggering raw path."""


class InventorySqlHold(Hold):
    def __init__(self, phase: str, error: sqlite3.Error):
        super().__init__("RESTORED_INVENTORY_SQL_UNVERIFIED")
        self.phase = phase
        self.code = getattr(error, "sqlite_errorcode", 0)


@contextmanager
def _inventory_sql(phase: str):
    try:
        yield
    except sqlite3.Error as exc:
        raise InventorySqlHold(phase, exc) from exc


def _safe_directory(path: Path) -> None:
    """Never follow a replaced parent component, even for metadata probes."""
    if not path.is_absolute() or ".." in path.parts:
        raise Hold("PARENT_PATH_UNSAFE")
    current = Path("/")
    for part in path.parts[1:]:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError as exc:
            raise Hold("PARENT_PATH_UNAVAILABLE") from exc
        if not stat.S_ISDIR(info.st_mode):
            raise Hold("PARENT_PATH_UNSAFE")


def _safe_file(path: Path, *, maximum: int, expected_owner: int | None = None):
    _safe_directory(path.parent)
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise Hold("SOURCE_FILE_UNAVAILABLE") from exc
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_size < 1 or info.st_size > maximum
            or info.st_mode & 0o002
            or (expected_owner is not None and info.st_uid != expected_owner)):
        raise Hold("SOURCE_FILE_UNSAFE")
    return info


def _operator_identity_guard() -> pwd.struct_passwd:
    """Only the pinned rootless operator, not sudo root, may open WAL/SHM."""
    try:
        account = pwd.getpwnam("porotaadmin")
    except KeyError as exc:
        raise Hold("ROOTLESS_SSH_IDENTITY_REQUIRED") from exc
    if (account.pw_name != "porotaadmin" or account.pw_uid != 1000
            or account.pw_gid != 1000 or account.pw_dir != "/home/porotaadmin"
            or os.getuid() != 1000 or os.geteuid() != 1000
            or os.getgid() != 1000 or os.getegid() != 1000):
        raise Hold("ROOTLESS_SSH_IDENTITY_REQUIRED")
    return account


def _release_marker(path: Path, release=CUSTODY_RELEASE) -> dict:
    source_sha, tree_sha, image_id = release
    info = _safe_file(path, maximum=16 * 1024)
    try:
        directory = _anchor_directory(path.parent)
        try:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                         dir_fd=directory)
            try:
                opened = os.fstat(fd)
                if ((opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
                        or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1):
                    raise Hold("RELEASE_PAPER_IDENTITY_UNVERIFIED")
                raw = os.read(fd, 16 * 1024 + 1)
                after = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
                if (len(raw) > 16 * 1024
                        or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)):
                    raise Hold("RELEASE_PAPER_IDENTITY_UNVERIFIED")
            finally:
                os.close(fd)
        finally:
            os.close(directory)
        marker = json.loads(raw)
    except (OSError, ValueError, TypeError) as exc:
        raise Hold("RELEASE_PAPER_IDENTITY_UNVERIFIED") from exc
    if (not isinstance(marker, dict)
            or marker.get("candidate_sha") != source_sha
            or marker.get("candidate_tree_sha") != tree_sha
            or marker.get("image_id") != image_id
            or marker.get("validation_status") != "VALIDATED_RUNTIME"
            or marker.get("mode") != "PRODUCTION_PAPER"
            or type(marker.get("real_orders_sent")) is not int
            or marker["real_orders_sent"] != 0
            or marker.get("real_order_capability") != "BLOCKED"
            or marker.get("real_order_routes") != "NOT_CALLED"
            or marker.get("deployment_backend") != "ROOTLESS_SPLIT"):
        raise Hold("RELEASE_PAPER_IDENTITY_UNVERIFIED")
    return marker


def host_preflight(db_path: Path = OBSERVER, marker_path: Path = MARKER,
                   custody_root: Path = CUSTODY, *, release=CUSTODY_RELEASE) -> dict:
    """Pure metadata read: never open SQLite or create the custody directory."""
    db_path, marker_path, custody_root = map(Path, (db_path, marker_path, custody_root))
    with _custody_parent_fd(custody_root):
        pass
    if custody_root.exists() or custody_root.is_symlink():
        _safe_directory(custody_root)
        info = custody_root.lstat()
        if info.st_mode & 0o077 or info.st_uid != os.getuid():
            raise Hold("CUSTODY_DIRECTORY_UNSAFE")
    source = _safe_file(db_path, maximum=MAX_DB_BYTES)
    wal = Path(str(db_path) + "-wal")
    shm = Path(str(db_path) + "-shm")
    try:
        wal_info = wal.lstat()
    except FileNotFoundError:
        wal_info = None
    try:
        shm_info = shm.lstat()
    except FileNotFoundError:
        shm_info = None
    for info, limit in ((wal_info, MAX_WAL_BYTES), (shm_info, 1024**2)):
        if info and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                     or info.st_uid != source.st_uid or info.st_gid != source.st_gid
                     or info.st_size > limit or info.st_mode & 0o002):
            raise Hold("SQLITE_SIDECAR_UNSAFE")
    if wal_info and wal_info.st_size > 0 and shm_info is None:
        raise Hold("SQLITE_SIDECAR_UNSAFE")
    _release_marker(marker_path, release)
    total = source.st_size + (wal_info.st_size if wal_info else 0)
    required = 3 * total + MIN_RESERVE_BYTES
    free = shutil.disk_usage(custody_root.parent).free
    if free < required:
        raise Hold("CUSTODY_DISK_RESERVE_BLOCKED")
    return {"schema": SCHEMA, "state": "PREFLIGHT_METADATA_PASS",
            "read_only": True, "host_mutations": 0,
            "source_bytes": source.st_size,
            "wal_bytes": wal_info.st_size if wal_info else 0,
            "shm_bytes": shm_info.st_size if shm_info else 0,
            "source_wal_state": ("PRESENT_UNRECONCILED" if wal_info and wal_info.st_size
                                 else "ABSENT_OR_EMPTY"),
            "paper_zero": "NO_VERIFICADO",  # Live WAL is intentionally unopened.
            "runtime_release_marker": "PINNED_P0_MARKER_ONLY",
            "free_bytes": free, "required_bytes": required,
            "ppi_requests": 0, "real_orders_sent_by_action": 0,
            "ppi_watch_touched": False}


@contextmanager
def _private_umask():
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


@contextmanager
def _custody_parent_fd(custody_root: Path):
    """Destination parent must be immutable to non-root/non-owner actors."""
    anchor = _anchor_directory(custody_root.parent)
    try:
        fd = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                     dir_fd=anchor)
        try:
            info = os.fstat(fd)
            if (info.st_uid != os.geteuid() or info.st_mode & 0o022
                    or not custody_root.name or custody_root.name in {".", ".."}):
                raise Hold("CUSTODY_DIRECTORY_UNSAFE")
            yield fd
        finally:
            os.close(fd)
    except OSError as exc:
        raise Hold("CUSTODY_DIRECTORY_UNSAFE") from exc
    finally:
        os.close(anchor)


@contextmanager
def _custody_lock(custody_root: Path):
    """Only the one-shot handler creates a private host lock."""
    with _custody_parent_fd(custody_root) as parent_fd:
        try:
            os.mkdir(custody_root.name, 0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except FileExistsError:
            pass
        except OSError as exc:
            raise Hold("CUSTODY_DIRECTORY_UNSAFE") from exc
        try:
            custody_fd = os.open(custody_root.name,
                                 os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                 dir_fd=parent_fd)
        except OSError as exc:
            raise Hold("CUSTODY_DIRECTORY_UNSAFE") from exc
        try:
            info = os.fstat(custody_fd)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise Hold("CUSTODY_DIRECTORY_UNSAFE")
            try:
                lock_fd = os.open("custody.lock", os.O_RDWR | os.O_CREAT |
                                  os.O_NOFOLLOW | os.O_CLOEXEC, 0o600,
                                  dir_fd=custody_fd)
            except OSError as exc:
                raise Hold("CUSTODY_LOCK_UNSAFE") from exc
            try:
                lock_meta = os.fstat(lock_fd)
                if (not stat.S_ISREG(lock_meta.st_mode) or lock_meta.st_nlink != 1
                        or lock_meta.st_uid != os.geteuid()
                        or stat.S_IMODE(lock_meta.st_mode) != 0o600):
                    raise Hold("CUSTODY_LOCK_UNSAFE")
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise Hold("CUSTODY_OWNER_BUSY") from exc
                yield custody_fd
            finally:
                os.close(lock_fd)
        finally:
            os.close(custody_fd)


def _live_paper_zero(db: sqlite3.Connection) -> None:
    try:
        db.execute("PRAGMA query_only=ON")  # Per-connection only; no source DDL.
        row = db.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
    except sqlite3.Error as exc:
        raise Hold("PAPER_ORDERS_UNVERIFIED") from exc
    if (row is None or row[0] != "PRODUCTION_PAPER"
            or type(row[1]) is not int or row[1] != 0):
        raise Hold("PAPER_ORDERS_UNVERIFIED")


def _exit_quiescence(db: sqlite3.Connection) -> str:
    """Bounded read sample; no inference of continuous worker inactivity."""
    try:
        _exit_deadline = time.monotonic() + 2
        db.set_progress_handler(lambda: int(time.monotonic() > _exit_deadline), 500)
        observer = db.execute(
            "SELECT process_state,session_state FROM observer_state WHERE id=1").fetchone()
        exit_reader = db.execute(
            "SELECT state FROM paper_exit_reader_state WHERE id=1").fetchone()
        if (not observer or observer[0] not in {"RUNNING", "WAITING_MARKET", "STOPPED"}
                or observer[1] not in {"CLOSED", "MARKET_CLOSED"}
                or not exit_reader or exit_reader[0] not in {"WAITING_MARKET", "STOPPED"}):
            raise Hold("EXIT_QUIESCENCE_UNVERIFIED")
        for statement in (
            "SELECT COUNT(*) FROM paper_positions WHERE status='OPEN'",
            "SELECT COUNT(*) FROM paper_future_positions WHERE status='ACTIVE'",
            "SELECT COUNT(*) FROM paper_cauciones WHERE status='OPEN'",
            "SELECT COUNT(*) FROM paper_family_lifecycle WHERE family='FCI' AND state!='REDEEMED'",
        ):
            count = db.execute(statement).fetchone()
            if not count or type(count[0]) is not int or count[0] != 0:
                raise Hold("EXIT_QUIESCENCE_UNVERIFIED")
        return "EXIT_WAITING_MARKET_CLOSED_FOUR_LEDGERS_FLAT_SAMPLED"
    except sqlite3.Error as exc:
        raise Hold("EXIT_QUIESCENCE_UNVERIFIED") from exc
    finally:
        db.set_progress_handler(None, 0)


def _anchor_directory(path: Path) -> int:
    """Keep every parent directory pinned to an O_NOFOLLOW descriptor."""
    if not path.is_absolute() or ".." in path.parts:
        raise Hold("PRIVILEGED_PATH_BINDING_UNVERIFIED")
    flags = os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = -1
    try:
        fd = os.open("/", flags)
        for part in path.parts[1:]:
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except OSError as exc:
        if fd >= 0:
            os.close(fd)
        raise Hold("PRIVILEGED_PATH_BINDING_UNVERIFIED") from exc


def _verified_db_inode(path: Path) -> tuple[int, int]:
    """No root-relative-follow or unchecked path substitution to DB inode."""
    directory_fd = _anchor_directory(path.parent)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                     dir_fd=directory_fd)
        try:
            info = os.fstat(fd)
            path_info = os.stat(path.name, dir_fd=directory_fd,
                                follow_symlinks=False)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_size < 1 or info.st_size > MAX_DB_BYTES
                    or (info.st_dev, info.st_ino) !=
                    (path_info.st_dev, path_info.st_ino)):
                raise Hold("RUNTIME_DB_BINDING_NO_VERIFICADO")
            return info.st_dev, info.st_ino
        finally:
            os.close(fd)
    except OSError as exc:
        raise Hold("RUNTIME_DB_BINDING_NO_VERIFICADO") from exc
    finally:
        os.close(directory_fd)


@contextmanager
def _anchored_source_sqlite(path: Path):
    """Pin parent+DB inode; resolve WAL next to its anchored directory fd."""
    descriptor = _anchor_directory(path.parent)
    try:
        file_fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                          dir_fd=descriptor)
        try:
            info = os.fstat(file_fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or not 0 < info.st_size <= MAX_DB_BYTES
                    or not re.fullmatch(r"[A-Za-z0-9_.-]+", path.name)):
                raise Hold("SOURCE_IDENTITY_CHANGED")
            expected = info.st_dev, info.st_ino
            def check():
                observed = os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
                if (not stat.S_ISREG(observed.st_mode)
                        or (observed.st_dev, observed.st_ino) != expected):
                    raise Hold("SOURCE_IDENTITY_CHANGED")
            check()
            with closing(sqlite3.connect(
                    f"file:/proc/self/fd/{descriptor}/{path.name}?mode=ro",
                    uri=True, timeout=2)) as source:
                # SQLite does not expose its fd. Identity checks bracketing the
                # open and copy reject ordinary replacement; no stability is
                # claimed across an adversarial intervening rename/revert.
                check()
                yield source
                check()
        finally:
            os.close(file_fd)
    except OSError as exc:
        raise Hold("SOURCE_IDENTITY_CHANGED") from exc
    finally:
        os.close(descriptor)


def _fsync_private_file(directory_fd: int, basename: str) -> None:
    try:
        fd = os.open(basename, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                     dir_fd=directory_fd)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) != 0o600):
                raise Hold("CUSTODY_DURABILITY_UNVERIFIED")
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as exc:
        raise Hold("CUSTODY_DURABILITY_UNVERIFIED") from exc


def _verify_private_state_group(descriptor: int, info, account: pwd.struct_passwd) -> None:
    """Reuse the dedicated-GID check from the rootless config-parent repair."""
    fail = "ROOTLESS_STATE_PRIVATE_GROUP_UNVERIFIED"
    if (info.st_uid, info.st_gid) != (account.pw_uid, account.pw_gid):
        raise Hold(fail)
    try:
        primary_group = grp.getgrgid(account.pw_gid)
        others = any(name != account.pw_name for name in primary_group.gr_mem)
        other_accounts = any(user.pw_uid != account.pw_uid and user.pw_gid == account.pw_gid
                             for user in pwd.getpwall())
    except (KeyError, OSError) as exc:
        raise Hold(fail) from exc
    if primary_group.gr_name != account.pw_name or others or other_accounts:
        raise Hold(fail)
    # An extended ACL can make the group-mode write bit apply to a named
    # foreign account. Keep that case fail-closed even for a dedicated GID.
    try:
        os.getxattr(descriptor, "system.posix_acl_access")
    except OSError as exc:
        if exc.errno in (errno.ENODATA, errno.ENOTSUP):
            return
        raise Hold(fail) from exc
    raise Hold(fail)


def _rootless_current_state() -> tuple[pwd.struct_passwd, dict]:
    """Read strictly private UID1000 deploy state with anchored nofollow fd."""
    try:
        account = _operator_identity_guard()
        home = Path(account.pw_dir)
        if not home.is_absolute() or ".." in home.parts:
            raise Hold("RUNTIME_DB_BINDING_NO_VERIFICADO")
        local_state = home / ".local/state"
        state = local_state / "porota-rc6"
        # This private state is the independent Deploy V2 source of both CIDs.
        for path in (home, home / ".local", local_state, state):
            descriptor = _anchor_directory(path)
            try:
                info = os.fstat(descriptor)
                owner_ok = (info.st_uid == 1000 if path in (home, state)
                            else info.st_uid in (0, 1000))
                forbidden = 0o077 if path == state else (0o002 if path == local_state else 0o022)
                if not owner_ok or info.st_mode & forbidden:
                    raise Hold("ROOTLESS_STATE_PARENT_UNSAFE")
                if path == local_state and info.st_mode & 0o020:
                    readable = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                       dir_fd=descriptor)
                    try:
                        _verify_private_state_group(readable, info, account)
                    finally:
                        os.close(readable)
            finally:
                os.close(descriptor)
        descriptor = _anchor_directory(state)
        try:
            fd = os.open("current.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                         dir_fd=descriptor)
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != 1000
                        or info.st_nlink != 1 or info.st_size > 131072
                        or info.st_mode & 0o077):
                    raise Hold("RUNTIME_DB_BINDING_NO_VERIFICADO")
                raw = os.read(fd, 131073)
                if len(raw) > 131072:
                    raise Hold("RUNTIME_DB_BINDING_NO_VERIFICADO")
                current = json.loads(raw)
            finally:
                os.close(fd)
        finally:
            os.close(descriptor)
        return account, current
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise Hold("RUNTIME_DB_BINDING_NO_VERIFICADO") from exc


def _rootless_client_environment(account: pwd.struct_passwd) -> dict[str, str]:
    home = Path(account.pw_dir)
    return {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C",
            "HOME": str(home), "USER": "porotaadmin", "LOGNAME": "porotaadmin",
            "XDG_RUNTIME_DIR": "/run/user/1000",
            "DOCKER_CONFIG": str(home / ".config/porota-rc6/client"),
            "DOCKER_CLI_HINTS": "false"}


def _docker_readonly_json(account: pwd.struct_passwd, *args: str):
    """Fixed UID1000/rootless socket, inspect/info ONLY; never host Docker."""
    _operator_identity_guard()
    if (not args or args[0] not in {"info", "container"}
            or args[0] == "container" and (len(args) != 3 or args[1] != "inspect")
            or args[0] == "info" and args[1:] != ("--format", "{{json .}}")):
        raise Hold("RUNTIME_DB_BINDING_NO_VERIFICADO")
    socket = Path("/run/user/1000/porota-rc6-docker.sock")
    try:
        runtime_fd = _anchor_directory(socket.parent)
        try:
            runtime = os.fstat(runtime_fd)
            sock = os.stat(socket.name, dir_fd=runtime_fd, follow_symlinks=False)
            if (runtime.st_uid != 1000 or runtime.st_mode & 0o022
                    or not stat.S_ISSOCK(sock.st_mode) or sock.st_uid != 1000
                    or sock.st_mode & 0o007):
                raise Hold("RUNTIME_DB_BINDING_NO_VERIFICADO")
        finally:
            os.close(runtime_fd)
        result = subprocess.run([
            "/usr/bin/docker",
            "--host", "unix:///run/user/1000/porota-rc6-docker.sock", *args],
            check=False, input=b"", stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=9, env=_rootless_client_environment(account), cwd="/")
        if result.returncode != 0 or len(result.stdout) > 131072:
            raise Hold("RUNTIME_DB_BINDING_NO_VERIFICADO")
        return json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError) as exc:
        raise Hold("RUNTIME_DB_BINDING_NO_VERIFICADO") from exc


def _verify_rootless_attestation(current: dict, daemon: dict, containers: dict,
                                 marker: dict, db_path: Path, *,
                                 data_root: Path = ROOT, release=CUSTODY_RELEASE,
                                 operator_home: Path = Path("/home/porotaadmin")) -> str:
    """Cross-bind marker ↔ private CIDs ↔ inspect ↔ exact host data inode."""
    fail = "RUNTIME_DB_BINDING_NO_VERIFICADO"
    source_sha, tree_sha, image_id = release
    if (not isinstance(current, dict) or not isinstance(daemon, dict)
            or not isinstance(containers, dict) or not isinstance(marker, dict)):
        raise Hold(fail)
    roles = current.get("roles")
    if (current.get("schema") != "porota.rootless.deploy.v1"
            or current.get("state") != "VALIDADO_RUNTIME"
            or current.get("operating_mode") != "PRODUCTION_PAPER"
            or current.get("execution_mode") != "SIMULATION"
            or type(current.get("real_orders_sent")) is not int
            or current["real_orders_sent"] != 0
            or current.get("source_sha") != source_sha
            or current.get("source_tree") != tree_sha
            or current.get("image_id") != image_id
            or current["source_sha"] != marker.get("candidate_sha")
            or current["source_tree"] != marker.get("candidate_tree_sha")
            or current["image_id"] != marker.get("image_id")
            or not isinstance(current.get("attempt"), str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", current["attempt"])
            or not isinstance(current.get("daemon_id"), str)
            or not current["daemon_id"]
            or not isinstance(roles, dict) or set(roles) != {"observer", "dashboard"}
            or any(not isinstance(cid, str) or not HEX64.fullmatch(cid)
                   for cid in roles.values())
            or len(set(roles.values())) != 2
            or (daemon.get("ID") != current["daemon_id"]
                or "name=rootless" not in (daemon.get("SecurityOptions") or [])
                or str(daemon.get("CgroupVersion")) != "2"
                or daemon.get("CgroupDriver") != "systemd"
                or daemon.get("DockerRootDir") != str(
                    operator_home / ".local/share/porota-rc6/docker"))
            or db_path not in (data_root / "paper_v17/observer_v17.db",
                               data_root / "market_history.db")):
        raise Hold(fail)
    for role in ("observer", "dashboard"):
        item = containers.get(role)
        if not isinstance(item, dict):
            raise Hold(fail)
        config = item.get("Config") or {}
        host = item.get("HostConfig") or {}
        labels = config.get("Labels") or {}
        state = item.get("State") or {}
        if (item.get("Id") != roles[role]
                or item.get("Name") != f"/porota-rc6-{role}-{current['attempt']}"
                or item.get("Image") != image_id
                or config.get("Image") != image_id
                or state.get("Status") != "running"
                or state.get("OOMKilled") is True
                or labels.get("io.porota.project") != "rc6"
                or labels.get("io.porota.role") != role
                or labels.get("io.porota.attempt") != current["attempt"]
                or config.get("User") != "0:0" or config.get("Entrypoint")
                or config.get("WorkingDir") != "/app"
                or config.get("Cmd") != ["python", "bv_paper_runtime.py" if role == "observer"
                                         else "o_dashboard.py"]
                or host.get("ReadonlyRootfs") is not True
                or host.get("Privileged") is not False
                or host.get("CapDrop") != ["ALL"] or host.get("CapAdd")
                or not any(option in {"no-new-privileges", "no-new-privileges:true"}
                           for option in (host.get("SecurityOpt") or []))
                or host.get("NetworkMode") in {"host", "none"}
                or str(host.get("NetworkMode", "")).startswith("container:")
                or host.get("PidMode") == "host"):
            raise Hold(fail)
        envs = config.get("Env")
        if not isinstance(envs, list) or any(not isinstance(line, str) for line in envs):
            raise Hold(fail)
        env = {}
        for line in envs:
            key, separator, value = line.partition("=")
            if (not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key)
                    or key in env or "\x00" in value):
                raise Hold(fail)
            env[key] = value
        required = {"POROTA_BUILD_SHA": source_sha,
                    "POROTA_CANDIDATE_TREE_SHA": tree_sha,
                    "PAPER_V17_DB_PATH": "/app/data/paper_v17/observer_v17.db",
                    "DASHBOARD_OPERATION_MODE": "PRODUCTION_PAPER",
                    "EXECUTION_MODE": "SIMULATION",
                    "REAL_ORDER_CAPABILITY": "BLOCKED"}
        if any(env.get(key) != value for key, value in required.items()):
            raise Hold(fail)
        if (db_path == data_root / "market_history.db"
                and env.get("HIST_DB_PATH", "/app/data/market_history.db")
                != "/app/data/market_history.db"):
            raise Hold(fail)
        mounts = item.get("Mounts")
        if not isinstance(mounts, list) or any(not isinstance(m, dict) for m in mounts):
            raise Hold(fail)
        expected = {"/app/data": (str(data_root), True),
                    "/app/operator": (str(operator_home / ".config/porota-rc6/operator"), False)}
        if role == "observer":
            expected["/run/secrets/ppi_production.json"] = (
                str(Path("/opt/porota-trading/.secrets/ppi_production.json")), False)
        bind = [mount for mount in mounts if mount.get("Type") == "bind"]
        if (len(bind) != len(expected)
                or {mount.get("Destination") for mount in bind} != set(expected)
                or any((mount.get("Source"), mount.get("RW")) !=
                       expected[mount["Destination"]] for mount in bind)
                or any(mount.get("Type") not in {"bind", "tmpfs"} for mount in mounts)):
            raise Hold(fail)
    # Anchor the actual host data and DB by fd (not a stale marker alone).
    try:
        _verified_db_inode(db_path)
    except Hold as exc:
        raise Hold(fail) from exc
    return "BOTH_ROOTLESS_CIDS_DATA_DB_INODE_MATCHED"


def _rootless_runtime_binding(marker: dict, db_path: Path, *, release=CUSTODY_RELEASE) -> str:
    """No Docker writes, no credential forwarding, no silent rootful fallback."""
    try:
        account, current = _rootless_current_state()
        daemon = _docker_readonly_json(account, "info", "--format", "{{json .}}")
        roles = current.get("roles") if isinstance(current, dict) else None
        if not isinstance(roles, dict) or set(roles) != {"observer", "dashboard"}:
            raise Hold("RUNTIME_DB_BINDING_NO_VERIFICADO")
        containers = {}
        for role in ("observer", "dashboard"):
            cid = roles[role]
            if not isinstance(cid, str) or not HEX64.fullmatch(cid):
                raise Hold("RUNTIME_DB_BINDING_NO_VERIFICADO")
            items = _docker_readonly_json(account, "container", "inspect", cid)
            if not isinstance(items, list) or len(items) != 1:
                raise Hold("RUNTIME_DB_BINDING_NO_VERIFICADO")
            containers[role] = items[0]
        result = _verify_rootless_attestation(current, daemon, containers, marker,
                                              db_path, operator_home=Path(account.pw_dir), release=release)
        if _rootless_current_state()[1] != current:
            raise Hold("RUNTIME_DB_BINDING_NO_VERIFICADO")
        return result
    except (OSError, KeyError, ValueError, TypeError, subprocess.TimeoutExpired) as exc:
        raise Hold("RUNTIME_DB_BINDING_NO_VERIFICADO") from exc


def _sha256(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            sha.update(block)
    return sha.hexdigest()


def _verify_restore(path: Path, deadline: float, *, database_role="OBSERVER") -> None:
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1",
                                 uri=True, timeout=1)) as restored:
        restored.execute("PRAGMA trusted_schema=OFF")
        restored.set_progress_handler(lambda: int(time.monotonic() > deadline), 5000)
        try:
            value = restored.execute("PRAGMA quick_check").fetchone()
        except sqlite3.Error as exc:
            raise Hold("PRIVATE_RESTORE_QUICK_CHECK_FAILED") from exc
        if not value or value[0] != "ok":
            raise Hold("PRIVATE_RESTORE_QUICK_CHECK_FAILED")
        if database_role == "OBSERVER":
            _live_paper_zero(restored)
        else:
            restored.execute("PRAGMA query_only=ON")
            tables = {row[0] for row in restored.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' AND name IN "
                "('history_versions_v2','history_canonical_v2','market_historical_ohlcv')")}
            if tables != {"history_versions_v2", "history_canonical_v2", "market_historical_ohlcv"}:
                raise Hold("PRIVATE_HISTORY_RESTORE_SCHEMA_UNVERIFIED")


def _inventory_days(mask: str) -> dict[str, str]:
    if (len(mask) != INVENTORY_LENGTH or not re.fullmatch(r"[012]+", mask)
            or "1" not in mask or "0" not in mask):
        raise Hold("RESTORED_INVENTORY_CALENDAR_UNVERIFIED")
    return {(INVENTORY_FROM + timedelta(days=offset)).isoformat(): status
            for offset, status in enumerate(mask)}


def _open_private_restore_member(directory: int, name: str, maximum: int):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                 dir_fd=directory)
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077
            or info.st_uid != os.geteuid() or not 0 < info.st_size <= maximum):
        os.close(fd)
        raise Hold("RESTORED_INVENTORY_BINDING_UNVERIFIED")
    return os.fdopen(fd, "rb"), info


@contextmanager
def _verified_restore(custody_root: Path, role: str, run_id: str, deadline: float, *,
                      maximum=MAX_DB_BYTES, expected_sha=None):
    if not RUN_ID.fullmatch(run_id):
        raise Hold("RESTORED_INVENTORY_BINDING_UNVERIFIED")
    directory = _anchor_directory(custody_root / f"{role.lower()}_{run_id}")
    try:
        folder = os.fstat(directory)
        if folder.st_mode & 0o077 or folder.st_uid != os.geteuid():
            raise Hold("RESTORED_INVENTORY_BINDING_UNVERIFIED")
        manifest, _ = _open_private_restore_member(directory, "manifest.json", 16 * 1024)
        with manifest:
            receipt = sanitize_receipt("CUSTODY_ONCE", json.load(manifest), remote_rc=0,
                                       expected_run_id=run_id, database_role=role)
        if receipt["state"] != "PRIVATE_SNAPSHOT_RESTORE_OK":
            raise Hold("RESTORED_INVENTORY_BINDING_UNVERIFIED")
        restored, info = _open_private_restore_member(directory, "restore-check.sqlite", maximum)
        with restored:
            if (info.st_size != receipt["restore_bytes"] or
                    (expected_sha is not None and expected_sha != receipt["restore_sha256"])):
                raise Hold("RESTORED_INVENTORY_DIGEST_MISMATCH")
            digest = hashlib.sha256()
            while block := restored.read(1024 * 1024):
                if time.monotonic() > deadline:
                    raise Hold("RESTORED_INVENTORY_TIMEOUT")
                digest.update(block)
            if digest.hexdigest() != receipt["restore_sha256"]:
                raise Hold("RESTORED_INVENTORY_DIGEST_MISMATCH")
            yield restored, receipt["restore_sha256"], info.st_size
            after = os.fstat(restored.fileno())
            if (after.st_mtime_ns, after.st_ctime_ns, after.st_size) != (
                    info.st_mtime_ns, info.st_ctime_ns, info.st_size):
                raise Hold("RESTORED_INVENTORY_BINDING_UNVERIFIED")
    finally:
        os.close(directory)


@contextmanager
def _restored_inventory_db(custody_root: Path, role: str, run_id: str, deadline: float):
    with _verified_restore(custody_root, role, run_id, deadline) as (restored, digest, _):
        uri = f"file:/proc/self/fd/{restored.fileno()}?mode=ro&immutable=1"
        with closing(sqlite3.connect(uri, uri=True, timeout=1)) as db:
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("PRAGMA query_only=ON")
            db.execute("PRAGMA temp_store=MEMORY")
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 5000)
            yield db, digest


def stream_restored_history(run_id: str, expected_sha: str, output, *, custody_root: Path = CUSTODY):
    """Stream only the attested private HISTORY restore into runner scratch."""
    if not HEX64.fullmatch(expected_sha):
        raise Hold("RESTORED_INVENTORY_DIGEST_MISMATCH")
    deadline = time.monotonic() + 165
    with _verified_restore(custody_root, "HISTORY", run_id, deadline,
                           maximum=MIGRATION_MAX_BYTES, expected_sha=expected_sha) as (source, _, size):
        source.seek(0)
        remaining = size
        while remaining:
            if time.monotonic() > deadline:
                raise Hold("RESTORED_INVENTORY_TIMEOUT")
            data = source.read(min(remaining, 1024 * 1024))
            if not data:
                raise Hold("RESTORED_INVENTORY_BINDING_UNVERIFIED")
            output.write(data)
            remaining -= len(data)


def _restored_history_counts(observer, history, days):
    with _inventory_sql("OBSERVER_PAPER_WITNESS"):
        _live_paper_zero(observer)
    with _inventory_sql("OBSERVER_CATALOG"):
        identities = observer.execute(
            """SELECT DISTINCT ticker,instrument_type,market,currency,settlement
                 FROM financial_instrument_catalog
                WHERE status='AVAILABLE' AND market='BYMA' AND currency='ARS'
                  AND instrument_type IN ('ACCIONES','CEDEARS')
                  AND settlement IN ('A-24HS','24HS','T1','T+1')
                  AND length(ticker) BETWEEN 1 AND 80 AND ticker=upper(trim(ticker))
                ORDER BY ticker,instrument_type,market,currency,settlement LIMIT ?""",
            (INVENTORY_IDENTITY_LIMIT + 1,)).fetchall()
    if not 0 < len(identities) <= INVENTORY_IDENTITY_LIMIT:
        raise Hold("RESTORED_INVENTORY_SCOPE_UNVERIFIED")
    with _inventory_sql("HISTORY_SCHEMA"):
        fields = {table: set(row[1] for row in history.execute(f"PRAGMA table_info({table})")) & expected
                  for table, expected in INVENTORY_TABLE_FIELDS.items()}
        missing = {table: sorted(expected - fields[table])
                   for table, expected in INVENTORY_TABLE_FIELDS.items()}
        conflict_table = history.execute("SELECT 1 FROM sqlite_schema WHERE type='table' "
                                         "AND name='history_source_conflicts_v2'").fetchone() is not None
    if any(missing.values()):
        cohorts, digest = _legacy_restored_history_counts(history, identities, days, fields)
        return cohorts, digest, conflict_table, missing
    # Both date range queries use the financial identity prefix of the existing
    # canonical and version indexes. Conflict lookup uses its leading version ID.
    where = """symbol=? AND instrument_type=? AND market=? AND currency=? AND settlement=?
               AND date>=? AND date<? AND price_basis='RAW'
               AND adjustment_basis='RAW_NO_ADJUSTMENT' AND adjusted=0"""
    conflict_count = ("SUM(EXISTS(SELECT 1 FROM history_source_conflicts_v2 AS c "
                      "WHERE c.current_version_id=v.id))" if conflict_table else "0")
    cohorts = {family: dict.fromkeys(INVENTORY_METRICS, 0) for family in INVENTORY_FAMILIES}
    digest = hashlib.sha256()
    certified = sum(status == "1" for status in days.values())
    for identity in identities:
        args = (*identity, INVENTORY_FROM.isoformat(), (INVENTORY_TO + timedelta(days=1)).isoformat())
        with _inventory_sql("HISTORY_CANONICAL"):
            canonical = {day: (version_id, exact) for day, version_id, exact in history.execute(
                """SELECT c.date,c.version_id,EXISTS(SELECT 1 FROM history_versions_v2 AS v
                        WHERE v.id=c.version_id AND v.date=c.date AND v.symbol=c.symbol
                          AND v.instrument_type=c.instrument_type AND v.market=c.market
                          AND v.currency=c.currency AND v.settlement=c.settlement
                          AND v.price_basis=c.price_basis AND v.adjustment_basis=c.adjustment_basis
                          AND v.adjusted=c.adjusted)
                     FROM history_canonical_v2 AS c WHERE """ + where, args)}
        with _inventory_sql("HISTORY_VERSIONS"):
            versions = {day: (count, conflicts) for day, count, conflicts in history.execute(
                "SELECT date,COUNT(*)," + conflict_count +
                " FROM history_versions_v2 AS v WHERE " + where + " GROUP BY date", args)}
        counts = cohorts[identity[1]]
        counts["identities"] += 1
        counts["with_canonical"] += any(exact and day in days for day, (_, exact) in canonical.items())
        counts["regular_or_certified_slots"] += certified
        counts["invalid_date_rows"] += len(canonical.keys() - days.keys()) + sum(
            versions[day][0] for day in versions.keys() - days.keys())
        counts["canonical_without_exact_version"] += sum(not exact for _, exact in canonical.values())
        counts["version_rows"] += sum(count for count, _ in versions.values())
        for day, status in days.items():
            version_id, exact = canonical.get(day, (None, 0))
            present = bool(exact)
            version_count, conflicted = versions.get(day, (0, 0))
            if status == "1":
                counts["canonical_on_certified_slots"] += present
                counts["absent_on_certified_slots"] += not present
            elif present:
                field = "canonical_on_uncertain_slots" if status == "2" else "canonical_outside_market_days"
                counts[field] += 1
            counts["conflicted_candidate_slots"] += bool(conflicted)
            digest.update((json.dumps([identity, day, status, version_id, exact, version_count, conflicted],
                                      separators=(",", ":")) + "\n").encode())
    return cohorts, digest.hexdigest(), conflict_table, missing


def _legacy_restored_history_counts(history, identities, days, fields):
    """Report broad dated matches; an incomplete key cannot certify coverage."""
    physical = fields["history_canonical_v2"]
    joinable = set(INVENTORY_JOIN_FIELDS).issubset(physical)
    cohorts = {family: dict.fromkeys(INVENTORY_METRICS) for family in INVENTORY_FAMILIES}
    for counts in cohorts.values():
        counts.update(identities=0, regular_or_certified_slots=0,
                      legacy_candidates_on_certified_slots=0 if joinable else None)
    digest = hashlib.sha256()
    certified = {day for day, status in days.items() if status == "1"}
    if joinable:
        clauses = ["symbol=?", "instrument_type=?", "market=?", "settlement=?", "date>=?", "date<?"]
        for field, fixed in (("currency", "ARS"), ("price_basis", "RAW"),
                             ("adjustment_basis", "RAW_NO_ADJUSTMENT"), ("adjusted", 0)):
            if field in physical:
                literal = "'" + fixed + "'" if isinstance(fixed, str) else str(fixed)
                clauses.append(f"{field}={literal}")
        query = "SELECT DISTINCT date FROM history_canonical_v2 WHERE " + " AND ".join(clauses)
    for symbol, family, market, currency, settlement in identities:
        counts = cohorts[family]
        counts["identities"] += 1
        counts["regular_or_certified_slots"] += len(certified)
        matches = set()
        if joinable:
            args = (symbol, family, market, settlement, INVENTORY_FROM.isoformat(),
                    (INVENTORY_TO + timedelta(days=1)).isoformat())
            with _inventory_sql("HISTORY_CANONICAL"):
                matches = {row[0] for row in history.execute(query, args)} & certified
            counts["legacy_candidates_on_certified_slots"] += len(matches)
        digest.update((json.dumps([symbol, family, market, currency, settlement, sorted(matches)],
                                  separators=(",", ":")) + "\n").encode())
    return cohorts, digest.hexdigest()


def host_restored_history_inventory(observer_run: str, history_run: str, mask: str, *,
                                    custody_root: Path = CUSTODY) -> dict:
    days = _inventory_days(mask)
    deadline = time.monotonic() + 145
    with _inventory_sql("OPEN_RESTORE"):
        with _restored_inventory_db(custody_root, "OBSERVER", observer_run, deadline) as observer:
            with _restored_inventory_db(custody_root, "HISTORY", history_run, deadline) as history:
                cohorts, digest, conflict_table, missing = _restored_history_counts(observer[0], history[0], days)
    if time.monotonic() > deadline:
        raise Hold("RESTORED_INVENTORY_TIMEOUT")
    return {"schema": INVENTORY_SCHEMA, "state": "RESTORED_HISTORY_INVENTORY_OK",
            "read_only": True, "source_sql_writes": 0, "ppi_requests": 0,
            "real_orders_sent_by_action": 0, "ppi_watch_touched": False,
            "observer_run": observer_run, "history_run": history_run,
            "observer_restore_sha256": observer[1], "history_restore_sha256": history[1],
            "calendar_sha256": hashlib.sha256(mask.encode()).hexdigest(),
            "inventory_sha256": digest, "cohorts": cohorts, "conflict_ledger_present": conflict_table,
            "missing_exact_columns": missing,
            "generated_at_utc": datetime.now(timezone.utc).isoformat()}


def sanitize_restored_inventory(raw, *, remote_rc, observer_run, history_run, mask):
    fallback = {"schema": INVENTORY_SCHEMA, "state": "NO_VERIFICADO", "read_only": True}
    if remote_rc:
        reason = raw.get("failure_reason") if isinstance(raw, dict) else None
        if isinstance(reason, str) and reason in PUBLIC_HOLD_REASONS:
            fallback["failure_reason"] = reason
        phase = raw.get("sql_phase") if isinstance(raw, dict) else None
        code = raw.get("sqlite_errorcode") if isinstance(raw, dict) else None
        if (isinstance(phase, str) and phase in INVENTORY_SQL_PHASES
                and type(code) is int and 0 <= code <= 65535):
            fallback.update(sql_phase=phase, sqlite_errorcode=code)
        return fallback
    days = _inventory_days(mask)
    fixed = {"schema": INVENTORY_SCHEMA, "state": "RESTORED_HISTORY_INVENTORY_OK",
             "read_only": True, "source_sql_writes": 0, "ppi_requests": 0,
             "real_orders_sent_by_action": 0, "ppi_watch_touched": False,
             "observer_run": observer_run, "history_run": history_run,
             "calendar_sha256": hashlib.sha256(mask.encode()).hexdigest()}
    extras = {"observer_restore_sha256", "history_restore_sha256", "inventory_sha256"}
    if (not isinstance(raw, dict) or set(raw) != fixed.keys() | extras | {
                "cohorts", "generated_at_utc", "conflict_ledger_present", "missing_exact_columns"}
            or any(type(raw[key]) is not type(value) or raw[key] != value for key, value in fixed.items())
            or any(not isinstance(raw[key], str) or not HEX64.fullmatch(raw[key]) for key in extras)
            or type(raw["conflict_ledger_present"]) is not bool
            or not isinstance(raw["cohorts"], dict) or set(raw["cohorts"]) != set(INVENTORY_FAMILIES)):
        return fallback
    try:
        stamp = datetime.fromisoformat(raw["generated_at_utc"])
        if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
            return fallback
    except (TypeError, ValueError):
        return fallback
    missing = raw["missing_exact_columns"]
    if (not isinstance(missing, dict) or set(missing) != set(INVENTORY_TABLE_FIELDS)
            or any(not isinstance(cols, list) or not all(isinstance(col, str) and col in INVENTORY_TABLE_FIELDS[table]
                                                         for col in cols) or cols != sorted(set(cols))
                   for table, cols in missing.items())):
        return fallback
    exact = not any(missing.values())
    legacy_unjoinable = not exact and bool(set(INVENTORY_JOIN_FIELDS) & set(missing["history_canonical_v2"]))
    for counts in raw["cohorts"].values():
        if (not isinstance(counts, dict) or set(counts) != set(INVENTORY_METRICS)
                or any(type(value) is not int or not 0 <= value <= 2**32
                       for key, value in counts.items() if exact or key not in INVENTORY_EXACT_METRICS
                       if key != "legacy_candidates_on_certified_slots" or not legacy_unjoinable)
                or not 0 <= counts["identities"] <= INVENTORY_IDENTITY_LIMIT
                or counts["regular_or_certified_slots"] != counts["identities"] * sum(s == "1" for s in days.values())):
            return fallback
        legacy_count = counts["legacy_candidates_on_certified_slots"]
        if exact:
            if (legacy_count != 0 or counts["with_canonical"] > counts["identities"]
                    or counts["canonical_on_certified_slots"] + counts["absent_on_certified_slots"]
                    != counts["regular_or_certified_slots"]):
                return fallback
        elif (any(counts[key] is not None for key in INVENTORY_EXACT_METRICS)
              or legacy_unjoinable and legacy_count is not None
              or not legacy_unjoinable and legacy_count > counts["regular_or_certified_slots"]):
            return fallback
    if not 0 < sum(counts["identities"] for counts in raw["cohorts"].values()) <= INVENTORY_IDENTITY_LIMIT:
        return fallback
    return raw


def source_metadata(db_path: Path) -> dict:
    """Metadata-only identity samples; WAL growth never implies no writers."""
    result = {}
    for label, path in (("db", db_path), ("wal", Path(str(db_path) + "-wal")),
                        ("shm", Path(str(db_path) + "-shm"))):
        try:
            info = path.lstat()
        except FileNotFoundError:
            result[label] = None
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise Hold("SOURCE_IDENTITY_CHANGED")
        result[label] = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    return result


def source_continuity(before: dict, after: dict) -> str:
    """Pin all existing inodes; disclose normal WAL/SHM content advancement."""
    if (not before.get("db") or not after.get("db")
            or before["db"][:2] != after["db"][:2]):
        raise Hold("SOURCE_IDENTITY_CHANGED")
    for label in ("wal", "shm"):
        if ((before.get(label) is None) != (after.get(label) is None)
                or before.get(label) is not None
                and before[label][:2] != after[label][:2]):
            raise Hold("SOURCE_IDENTITY_CHANGED")
    wal_state = "UNCHANGED" if before.get("wal") == after.get("wal") else "CHANGED"
    shm_state = "UNCHANGED" if before.get("shm") == after.get("shm") else "CHANGED"
    return f"DB_IDENTITY_STABLE_WAL_{wal_state}_SHM_{shm_state}"


def host_custody_once(db_path: Path = OBSERVER, marker_path: Path = MARKER,
                      custody_root: Path = CUSTODY, *, run_id: str,
                      paper_db_path: Path | None = None, release=CUSTODY_RELEASE) -> dict:
    """Explicit writer mode: one private online backup + separate restore check.

    Never copy the base file or WAL bytewise. The SQLite backup API merges
    committed WAL transactions into a consistent destination snapshot.
    Partial custody directories are intentionally retained on interruption.
    """
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise Hold("CUSTODY_RUN_ID_INVALID")
    db_path, marker_path, custody_root = map(Path, (db_path, marker_path, custody_root))
    database_role = "HISTORY" if paper_db_path is not None else "OBSERVER"
    if db_path in DATABASES.values():
        _operator_identity_guard()
    if db_path == HISTORY and paper_db_path != OBSERVER:
        raise Hold("PAPER_WITNESS_REQUIRED_FOR_HISTORY")
    before = host_preflight(db_path, marker_path, custody_root, release=release)
    runtime_binding = _rootless_runtime_binding(_release_marker(marker_path, release), db_path, release=release)
    source_before = source_metadata(db_path)
    deadline = time.monotonic() + BACKUP_TIMEOUT_SECONDS
    # A real WAL reader may briefly affect -shm locking; this path requires
    # the separately attested DEPLOY_OWNER and cannot be run by preflight.
    try:
        with ExitStack() as connections:
            source = connections.enter_context(_anchored_source_sqlite(db_path))
            paper = (source if paper_db_path is None else
                     connections.enter_context(_anchored_source_sqlite(paper_db_path)))
            source.execute("PRAGMA busy_timeout=200")
            source.execute("PRAGMA query_only=ON")
            _live_paper_zero(paper)
            exit_quiescence = _exit_quiescence(paper)
            final_name, partial_name = database_role.lower() + "_" + run_id, ".partial_" + run_id
            final = custody_root / final_name
            partial = custody_root / partial_name
            if final.exists() or final.is_symlink() or partial.exists() or partial.is_symlink():
                raise Hold("CUSTODY_RUN_ALREADY_EXISTS")
            with _private_umask(), _custody_lock(custody_root) as custody_fd:
                try:
                    os.mkdir(partial_name, 0o700, dir_fd=custody_fd)
                except FileExistsError as exc:
                    raise Hold("CUSTODY_RUN_ALREADY_EXISTS") from exc
                os.fsync(custody_fd)  # Partial creation must survive reboot.
                part_fd = os.open(partial_name,
                                  os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW |
                                  os.O_CLOEXEC, dir_fd=custody_fd)
                try:
                    # This private fd path is never under the product's RW
                    # /app/data bind. No DB base-file/WAL byte copying.
                    snapshot = Path(f"/proc/self/fd/{part_fd}/snapshot.sqlite")
                    restored = Path(f"/proc/self/fd/{part_fd}/restore-check.sqlite")
                    with source, closing(sqlite3.connect(snapshot, timeout=1)) as target:
                        wal_mode = source.execute("PRAGMA journal_mode").fetchone() == ("wal",)
                        if wal_mode:
                            # Pin one read view as in A729. Without it, each
                            # concurrent WAL commit can restart a paged backup.
                            source.execute("BEGIN")
                            source.execute("SELECT name FROM sqlite_schema LIMIT 1").fetchone()

                        def progress(status, remaining, total):
                            if time.monotonic() > deadline:
                                raise Hold("CUSTODY_SQLITE_BACKUP_TIMEOUT")
                            if wal_mode:
                                wal = source_metadata(db_path)["wal"]
                                if wal is not None and wal[2] > MAX_WAL_BYTES:
                                    raise Hold("SQLITE_SIDECAR_UNSAFE")

                        source.backup(target, pages=256, sleep=0.1, progress=progress)
                    # Release the read view before fsync, restore checks or
                    # refreshing the live PAPER and exit witnesses.
                    os.chmod("snapshot.sqlite", 0o600, dir_fd=part_fd,
                             follow_symlinks=False)
                    _fsync_private_file(part_fd, "snapshot.sqlite")
                    _live_paper_zero(paper)
                    if _exit_quiescence(paper) != exit_quiescence:
                        raise Hold("EXIT_QUIESCENCE_UNVERIFIED")
                    if (_rootless_runtime_binding(_release_marker(marker_path, release), db_path, release=release)
                            != runtime_binding):
                        raise Hold("RUNTIME_DB_BINDING_NO_VERIFICADO")
                    if time.monotonic() > deadline:
                        raise Hold("CUSTODY_BACKUP_TIMEOUT")
                    snapshot_digest = _sha256(snapshot)
                    restore_fd = os.open("restore-check.sqlite", os.O_WRONLY | os.O_CREAT |
                                         os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                         0o600, dir_fd=part_fd)
                    with snapshot.open("rb") as original, os.fdopen(restore_fd, "wb") as copy:
                        shutil.copyfileobj(original, copy, length=1024 * 1024)
                        copy.flush()
                        os.fsync(copy.fileno())
                    _fsync_private_file(part_fd, "restore-check.sqlite")
                    restore_digest = _sha256(restored)
                    if snapshot_digest != restore_digest:
                        raise Hold("PRIVATE_RESTORE_DIGEST_MISMATCH")
                    _verify_restore(restored, deadline, database_role=database_role)
                    if time.monotonic() > deadline:
                        raise Hold("CUSTODY_BACKUP_TIMEOUT")
                    continuity = source_continuity(source_before, source_metadata(db_path))
                    _release_marker(marker_path, release)  # Same pinned release after copy.
                    receipt = {"schema": SCHEMA, "state": "PRIVATE_SNAPSHOT_RESTORE_OK",
                               "read_only": False, "source_sql_writes": 0,
                               "shm_locking_possible": True,
                               "runtime_db_binding": runtime_binding,
                               "exit_quiescence": exit_quiescence,
                               "paper_zero": "OBSERVED", "run_id": run_id,
                               "source_bytes": before["source_bytes"],
                               "wal_bytes_before": before["wal_bytes"],
                               "source_wal_state": ("PRESENT_RECONCILED_BY_SQLITE_BACKUP"
                                                    if before["wal_bytes"] else "SOURCE_WAL_ABSENT"),
                               "snapshot_bytes": snapshot.stat().st_size,
                               "source_identity_continuity": continuity,
                               "snapshot_sha256": snapshot_digest,
                               "restore_bytes": restored.stat().st_size,
                               "restore_sha256": restore_digest,
                               "restore_quick_check": "ok",
                               "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                               "host_mutations": 1,
                               "ppi_requests": 0, "real_orders_sent_by_action": 0,
                               "ppi_watch_touched": False}
                    if database_role == "HISTORY":
                        receipt["database_role"] = database_role
                    manifest_fd = os.open("manifest.json", os.O_WRONLY | os.O_CREAT |
                                          os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                          0o600, dir_fd=part_fd)
                    with os.fdopen(manifest_fd, "w", encoding="utf-8") as manifest:
                        json.dump(receipt, manifest, sort_keys=True)
                        manifest.flush()
                        os.fsync(manifest.fileno())
                    os.fsync(part_fd)  # Three data files + directory durable.
                    os.rename(partial_name, final_name,
                              src_dir_fd=custody_fd, dst_dir_fd=custody_fd)
                    os.fsync(custody_fd)  # Atomic final-directory rename durable.
                    return receipt
                except OSError as exc:
                    raise Hold("CUSTODY_DURABILITY_UNVERIFIED") from exc
                finally:
                    os.close(part_fd)
    except sqlite3.Error as exc:
        raise Hold("CUSTODY_SQLITE_UNAVAILABLE") from exc


def sanitize_receipt(mode: str, raw: dict, *, remote_rc: int,
                     expected_run_id: str | None = None, database_role="OBSERVER") -> dict:
    """Runner-side allowlist; refuses unexpected content, never echoes raw text."""
    if mode not in {"PREFLIGHT_ONLY", "CUSTODY_ONCE"} or database_role not in DATABASES:
        raise Hold("MODE_UNSUPPORTED")
    fallback = {"schema": SCHEMA, "state": "NO_VERIFICADO_HOST_COMMAND"
                if remote_rc else "NO_VERIFICADO_RECEIPT_UNSAFE",
                "read_only": mode == "PREFLIGHT_ONLY",
                "source_sql_writes": None, "paper_zero": "NO_VERIFICADO",
                "ppi_requests": 0, "ppi_watch_touched": False}
    if remote_rc != 0:
        if (isinstance(raw, dict) and set(raw) == {"schema", "failure_reason"}
                and raw["schema"] == SCHEMA and isinstance(raw["failure_reason"], str)
                and raw["failure_reason"] in PUBLIC_HOLD_REASONS):
            fallback["failure_reason"] = raw["failure_reason"]
        return fallback
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        return fallback
    shared = {"schema", "state", "read_only", "paper_zero",
              "ppi_requests", "real_orders_sent_by_action", "ppi_watch_touched"}
    preflight = {"host_mutations", "source_bytes", "wal_bytes", "shm_bytes",
                 "source_wal_state", "runtime_release_marker", "free_bytes",
                 "required_bytes"}
    custody = {"source_sql_writes", "shm_locking_possible", "run_id", "source_bytes", "wal_bytes_before",
               "source_wal_state", "snapshot_bytes", "snapshot_sha256",
               "restore_bytes", "restore_sha256", "restore_quick_check",
               "generated_at_utc", "host_mutations", "source_identity_continuity",
               "runtime_db_binding", "exit_quiescence"}
    allowed = shared | (preflight if mode == "PREFLIGHT_ONLY" else custody)
    if database_role == "HISTORY":
        allowed.add("database_role")
        if raw.get("database_role") != database_role:
            return fallback
    if set(raw) != allowed or raw.get("ppi_requests") != 0 or raw.get("ppi_watch_touched") is not False:
        return fallback
    if type(raw.get("real_orders_sent_by_action")) is not int or raw["real_orders_sent_by_action"] != 0:
        return fallback
    number_names = ("source_bytes", "wal_bytes", "shm_bytes", "free_bytes", "required_bytes") \
        if mode == "PREFLIGHT_ONLY" else ("source_bytes", "wal_bytes_before",
                                          "snapshot_bytes", "restore_bytes")
    if any(type(raw.get(name)) is not int or not 0 <= raw[name] <= 2**60
           for name in number_names):
        return fallback
    if mode == "PREFLIGHT_ONLY":
        if (raw.get("state") != "PREFLIGHT_METADATA_PASS"
                or raw.get("read_only") is not True or raw.get("host_mutations") != 0
                or raw.get("paper_zero") != "NO_VERIFICADO"
                or raw.get("source_wal_state") not in {"PRESENT_UNRECONCILED", "ABSENT_OR_EMPTY"}
                or raw.get("runtime_release_marker") != "PINNED_P0_MARKER_ONLY"
                or raw["source_bytes"] < 1 or raw["required_bytes"] > raw["free_bytes"]):
            return fallback
    else:
        try:
            stamped = datetime.fromisoformat(raw["generated_at_utc"])
        except (ValueError, TypeError, KeyError):
            return fallback
        if (raw.get("state") != "PRIVATE_SNAPSHOT_RESTORE_OK"
                or raw.get("read_only") is not False or raw.get("host_mutations") != 1
                or raw.get("source_sql_writes") != 0
                or raw.get("shm_locking_possible") is not True
                or raw.get("runtime_db_binding") != "BOTH_ROOTLESS_CIDS_DATA_DB_INODE_MATCHED"
                or raw.get("exit_quiescence") != "EXIT_WAITING_MARKET_CLOSED_FOUR_LEDGERS_FLAT_SAMPLED"
                or raw.get("paper_zero") != "OBSERVED"
                or raw.get("source_wal_state") not in {
                    "PRESENT_RECONCILED_BY_SQLITE_BACKUP", "SOURCE_WAL_ABSENT"}
                or raw.get("source_identity_continuity") not in {
                    "DB_IDENTITY_STABLE_WAL_UNCHANGED_SHM_UNCHANGED",
                    "DB_IDENTITY_STABLE_WAL_CHANGED_SHM_UNCHANGED",
                    "DB_IDENTITY_STABLE_WAL_UNCHANGED_SHM_CHANGED",
                    "DB_IDENTITY_STABLE_WAL_CHANGED_SHM_CHANGED"}
                or not isinstance(raw.get("run_id"), str)
                or not RUN_ID.fullmatch(raw["run_id"])
                or not isinstance(expected_run_id, str)
                or not RUN_ID.fullmatch(expected_run_id)
                or raw["run_id"] != expected_run_id
                or raw.get("restore_quick_check") != "ok"
                or not all(isinstance(raw.get(k), str) and HEX64.fullmatch(raw[k])
                           for k in ("snapshot_sha256", "restore_sha256"))
                or raw["snapshot_sha256"] != raw["restore_sha256"]
                or raw["snapshot_bytes"] != raw["restore_bytes"]
                or raw["snapshot_bytes"] < 1
                or stamped.tzinfo is None or stamped.utcoffset().total_seconds() != 0):
            return fallback
    return dict(raw)  # Exact allowlist validated; never reproduce unknown fields.


def _field(body: str, key: str) -> str:
    hits = re.findall(r"^" + re.escape(key) + r": *([^\n]+)$", body, flags=re.MULTILINE)
    if len(hits) != 1 or not hits[0].strip():
        raise Hold("DEPLOY_OWNER_METADATA_INVALID")
    return hits[0].strip()


def validate_deploy_owner(receipt: dict, comments: list[dict], *, database_role="OBSERVER", host_scope=None) -> str:
    """Separate current global DEPLOY_OWNER lease, independent of WRITE_OWNER."""
    if (not isinstance(receipt, dict) or type(receipt.get("id")) is not int
            or receipt.get("issue_url") != OWNER_ISSUE
            or receipt.get("html_url") != OWNER_PUBLIC + str(receipt["id"])
            or receipt.get("user", {}).get("login") != "mbalbo2023"):
        raise Hold("DEPLOY_OWNER_RECEIPT_INVALID")
    body = receipt.get("body") or ""
    if ("DEPLOY_OWNER" not in body
            or _field(body, "Ownership role") != "DEPLOY_OWNER"
            or _field(body, "Ownership status") != "ACTIVE"
            or database_role not in DATABASES
            or _field(body, "Host scope") != (host_scope or f"RC6_{database_role}_ONLINE_CUSTODY_ONCE")):
        raise Hold("DEPLOY_OWNER_RECEIPT_INVALID")
    workstream = _field(body, "Workstream ID")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{1,79}", workstream):
        raise Hold("DEPLOY_OWNER_RECEIPT_INVALID")
    if not isinstance(comments, list) or len(comments) > 3000:
        raise Hold("DEPLOY_OWNER_LEDGER_UNAVAILABLE")
    found = False
    latest = {}
    for comment in comments:
        if not isinstance(comment, dict) or type(comment.get("id")) is not int:
            raise Hold("DEPLOY_OWNER_LEDGER_UNAVAILABLE")
        if comment["id"] == receipt["id"]:
            found = (comment.get("body") == body
                     and comment.get("html_url") == receipt["html_url"]
                     and comment.get("user", {}).get("login") == "mbalbo2023")
        text = comment.get("body") or ""
        if (comment.get("user", {}).get("login") != "mbalbo2023"
                or "Ownership role: DEPLOY_OWNER" not in text):
            continue
        try:
            identity = _field(text, "Workstream ID")
            status = _field(text, "Ownership status")
        except Hold as exc:
            raise Hold("DEPLOY_OWNER_LEDGER_INVALID") from exc
        if status not in {"ACTIVE", "RELEASED", "SUPERSEDED"}:
            raise Hold("DEPLOY_OWNER_LEDGER_INVALID")
        if identity == workstream and comment["id"] > receipt["id"]:
            raise Hold("DEPLOY_OWNER_NOT_CURRENT")
        if identity not in latest or comment["id"] > latest[identity][0]:
            latest[identity] = (comment["id"], status)
    if not found:
        raise Hold("DEPLOY_OWNER_NOT_IN_LEDGER")
    if any(identity != workstream and decision[1] == "ACTIVE"
           for identity, decision in latest.items()):
        raise Hold("DEPLOY_OWNER_OVERLAP")
    return workstream


def verify_owner_from_github(receipt_id: str, *, database_role="OBSERVER", host_scope=None) -> str:
    """Runner-only GitHub lease validation, never executed on the droplet."""
    if not re.fullmatch(r"[1-9][0-9]{5,15}", receipt_id):
        raise Hold("DEPLOY_OWNER_ID_INVALID")
    token = os.environ.get("GH_TOKEN")
    if not token:
        raise Hold("DEPLOY_OWNER_TOKEN_UNAVAILABLE")
    api = f"https://api.github.com/repos/{OWNER_BRANCH}"

    def fetch(url: str, maximum: int):
        request = urllib.request.Request(url, headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"})
        with urllib.request.urlopen(request, timeout=10) as response:
            data = response.read(maximum + 1)
        if len(data) > maximum:
            raise Hold("DEPLOY_OWNER_API_RESPONSE_TOO_LARGE")
        return json.loads(data)

    try:
        receipt = fetch(api + "/issues/comments/" + receipt_id, 64 * 1024)
        comments = []
        for page in range(1, 31):
            batch = fetch(api + f"/issues/980/comments?per_page=100&page={page}",
                          6 * 1024 * 1024)
            if not isinstance(batch, list) or len(batch) > 100:
                raise Hold("DEPLOY_OWNER_LEDGER_UNAVAILABLE")
            comments.extend(batch)
            if len(batch) < 100:
                break
        else:
            raise Hold("DEPLOY_OWNER_LEDGER_UNAVAILABLE")
    except (OSError, ValueError, TypeError) as exc:
        raise Hold("DEPLOY_OWNER_API_UNAVAILABLE") from exc
    return validate_deploy_owner(receipt, comments, database_role=database_role, host_scope=host_scope)


def rehearse_restored_history(database: Path, product: Path, run_id: str, expected_sha: str) -> dict:
    if (os.environ.get("GITHUB_ACTIONS") != "true" or not os.environ.get("RUNNER_TEMP")
            or not RUN_ID.fullmatch(run_id) or not HEX64.fullmatch(expected_sha)):
        raise Hold("OFFHOST_RESTORE_BINDING_UNVERIFIED")
    runner_root = Path(os.environ["RUNNER_TEMP"]).resolve()
    if (not database.is_file() or database.is_symlink()
            or not database.resolve().is_relative_to(runner_root)):
        raise Hold("OFFHOST_RESTORE_BINDING_UNVERIFIED")
    size = database.stat().st_size
    if not 4096 <= size <= MIGRATION_MAX_BYTES:
        raise Hold("OFFHOST_RESTORE_SIZE_UNVERIFIED")
    with _private_umask(), (database.parent / "migration-capture.json").open("x") as capture:
        json.dump({"source_sha256": expected_sha, "source_bytes": size}, capture)
    result = subprocess.run(
        [sys.executable, "-B", "-m", "scripts.rc6_history_offhost_migration_rehearsal",
         "--database", str(database), "--capture-report", str(database.parent / "migration-capture.json"),
         "--runner-root", str(runner_root), "--seconds", "650"],
        cwd=product, capture_output=True, text=True, timeout=670, check=False)
    if result.returncode:
        reason = re.search(r"(?m)^[\w.]+(?:Error|Exception): ((?:OFFHOST|HISTORY|SCRATCH|SOURCE)_[A-Z0-9_]{1,90})\s*$",
                           result.stderr)
        raise Hold(reason[1] if reason else "OFFHOST_REHEARSAL_FAILED")
    report = json.loads(result.stdout) if len(result.stdout) <= 8192 else {}
    counts = [report.get(key) for key in ("legacy_versions", "mapped_versions", "quarantined_versions")]
    if (report.get("schema") != MIGRATION_SCHEMA or report.get("source_sha256") != expected_sha
            or report.get("source_bytes") != size
            or any(type(value) is not int or value < 0 for value in counts)
            or counts[0] != counts[1] + counts[2]
            or not HEX64.fullmatch(str(report.get("migrated_copy_sha256", "")))
            or report.get("original_source_mutated") is not False
            or report.get("temporary_copy_removed") is not True
            or report.get("host_promotion_allowed") is not False
            or report.get("physical_purge_allowed") is not False):
        raise Hold("OFFHOST_REHEARSAL_RECEIPT_UNVERIFIED")
    return {"schema": MIGRATION_SCHEMA, "state": "RESTORED_HISTORY_MIGRATION_REHEARSED",
            "history_run": run_id, "source_bytes": size, "source_sha256": expected_sha,
            "migrated_copy_sha256": report["migrated_copy_sha256"], "max_source_bytes": MIGRATION_MAX_BYTES,
            "legacy_versions": counts[0], "mapped_versions": counts[1], "quarantined_versions": counts[2],
            "source_sql_writes": 0, "host_promotion_allowed": False, "physical_purge_allowed": False,
            "cross_database_pins": "NO_VERIFICADO"}


def _history_release(source, tree, image):
    if (not re.fullmatch(r"[0-9a-f]{40}", source) or not re.fullmatch(r"[0-9a-f]{40}", tree)
            or not image.startswith("sha256:") or not HEX64.fullmatch(image[7:])):
        raise Hold("HISTORY_IMPORT_RELEASE_INVALID")
    return source, tree, image


@contextmanager
def _history_eod_lock():
    lock_path = HISTORY.with_suffix(HISTORY.suffix + ".local-eod.lock")
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
            raise Hold("HISTORY_IMPORT_LOCK_UNSAFE")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Hold("HISTORY_IMPORT_WRITER_BUSY") from exc
        yield
    finally:
        os.close(fd)


def _history_source_pin():
    meta = source_metadata(HISTORY)
    wal = meta["wal"]
    return {"db": list(meta["db"]), "wal": list(wal) if wal and wal[2] else None}


def _history_folder(run_id):
    if not RUN_ID.fullmatch(run_id):
        raise Hold("HISTORY_IMPORT_RUN_ID_INVALID")
    path = CUSTODY / ("history_" + run_id)
    fd = _anchor_directory(path)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise Hold("HISTORY_IMPORT_DIRECTORY_UNSAFE")
    finally:
        os.close(fd)
    return path


def prepare_reference_history(run_id, release):
    _operator_identity_guard()
    with _history_eod_lock():
        before = _history_source_pin()
        receipt = host_custody_once(HISTORY, run_id=run_id, paper_db_path=OBSERVER, release=release)
        if _history_source_pin() != before:
            raise Hold("HISTORY_IMPORT_SOURCE_CHANGED")
        folder = _history_folder(run_id)
        cert, key = folder / "transfer.crt", folder / "transfer.key"
        with _private_umask():
            subprocess.run(
                ["/usr/bin/openssl", "req", "-new", "-x509", "-newkey", "rsa:3072", "-nodes",
                 "-days", "1", "-subj", "/CN=RC6-HISTORY-ONE-SHOT", "-keyout", str(key), "-out", str(cert)],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
            with (folder / "reference-apply.json").open("x", encoding="utf-8") as f:
                json.dump({"release": release, "source": before, "restore_sha256": receipt["restore_sha256"]}, f)
                f.flush()
                os.fsync(f.fileno())
        return {"schema": "rc6.history-reference-transfer.v1", "state": "HISTORY_REFERENCE_READY",
                "run_id": run_id, "restore_sha256": receipt["restore_sha256"],
                "certificate_b64": base64.b64encode(cert.read_bytes()).decode("ascii")}


HISTORY_LOADER = r"""
from contextlib import closing
from hashlib import file_digest
from pathlib import Path
import errno
import json
import os
import re
import shutil
import sqlite3
import sys
import tarfile
import tempfile

def report_loader_failure(error_type, error, _traceback):
    code = str(error) if isinstance(error, ValueError) else ''
    if not re.fullmatch(r'HISTORY_[A-Z0-9_]{3,85}', code):
        if isinstance(error, sqlite3.Error):
            name = getattr(error, 'sqlite_errorname', '') or ''
            code = ('HISTORY_IMPORT_' + name if re.fullmatch(r'SQLITE_[A-Z0-9_]{2,55}', name)
                    else 'HISTORY_IMPORT_SQLITE_ERROR')
        elif isinstance(error, OSError):
            code = 'HISTORY_IMPORT_OS_' + errno.errorcode.get(error.errno, 'ERROR')
        elif isinstance(error, ImportError):
            code = 'HISTORY_IMPORT_PRODUCT_MODULE_UNAVAILABLE'
        elif isinstance(error, tarfile.TarError):
            code = 'HISTORY_IMPORT_CAPSULE_INVALID'
        else:
            code = 'HISTORY_IMPORT_PRODUCT_EXCEPTION'
    print(code, file=sys.stderr)

sys.excepthook = report_loader_failure
from scripts.rc6_history_import_source_reported import import_iol_export

destination = Path('/app/data/market_history.db')
sqlite_scratch = Path(os.environ.get('SQLITE_TMPDIR', ''))
if sqlite_scratch.parent != destination.parent or not sqlite_scratch.is_dir():
    raise SystemExit('HISTORY_IMPORT_SQLITE_SCRATCH_UNBOUND')
identity = tuple(map(int, sys.argv[1].split(':')))
legacy = int(sys.argv[2])
pins = json.loads(sys.argv[3])
with tempfile.TemporaryDirectory(prefix='rc6-iol-', dir='/tmp') as folder:
    storage = Path(folder)
    names = set()
    with tarfile.open(fileobj=sys.stdin.buffer, mode='r|') as bundle:
        for member in bundle:
            if member.name not in pins or member.name in names or not member.isreg() or not 0 < member.size <= 16*1024**2:
                raise SystemExit('HISTORY_IMPORT_CAPSULE_INVALID')
            names.add(member.name)
            with bundle.extractfile(member) as src, (storage / member.name).open('xb') as dst:
                shutil.copyfileobj(src, dst, 1024*1024)
    if names != set(pins):
        raise SystemExit('HISTORY_IMPORT_CAPSULE_INVALID')
    for name, (digest, _) in pins.items():
        with (storage / name).open('rb') as f:
            if file_digest(f, 'sha256').hexdigest() != digest:
                raise SystemExit('HISTORY_IMPORT_CAPTURE_DIGEST_MISMATCH')
    results = {}
    for name, (digest, expected) in pins.items():
        result = import_iol_export(storage / name, destination, digest, existing_identity=identity,
                                   expected_legacy_versions=legacy if name == 'cutoff.zip' else None)
        if result.get('committed_rows') != expected or result['readiness_implication'] != 'NONE':
            raise SystemExit('HISTORY_IMPORT_ACCOUNTING_MISMATCH')
        results[name] = result
    with closing(sqlite3.connect(destination.as_uri()+'?mode=ro', uri=True, timeout=1)) as c:
        if c.execute('PRAGMA foreign_key_check').fetchone():
            raise SystemExit('HISTORY_IMPORT_FOREIGN_KEY_FAILED')
        immutable_count = c.execute('SELECT COUNT(*) FROM history_versions_v2_legacy').fetchone()[0]
        if immutable_count != legacy:
            raise SystemExit('HISTORY_IMPORT_LEGACY_COUNT_CHANGED')
    stat = destination.stat()
    if (stat.st_dev, stat.st_ino) != identity:
        raise SystemExit('HISTORY_IMPORT_INODE_CHANGED')
    print(json.dumps({
        'source_reported_committed_rows': sum(result['committed_rows'] for result in results.values()),
        'versions_appended': sum(result['versions_appended'] for result in results.values()),
        'canonical_updates': sum(result['canonical_updates'] for result in results.values()),
        'legacy_versions_preserved': immutable_count,
        'preparation': results['cutoff.zip']['schema_upgrade']['state'],
        'quick_check': 'ok',
    }))
"""


def _history_loader_failure(returncode, stderr):
    cause = re.search(rb'\b(HISTORY_[A-Z0-9_]{3,85})\s*$', stderr.strip())
    if cause:
        return cause[1].decode()
    if returncode in {-9, 137}:
        return "HISTORY_IMPORT_CONTAINER_PROCESS_KILLED"
    if b"Cannot connect to the Docker daemon" in stderr:
        return "HISTORY_IMPORT_ROOTLESS_SOCKET_UNAVAILABLE"
    if b"OCI runtime exec failed" in stderr or returncode in {125, 126, 127}:
        return "HISTORY_IMPORT_CONTAINER_EXEC_UNAVAILABLE"
    return "HISTORY_IMPORT_DOCKER_FAILED"


def _execute_history_loader(account, cid, bundle, identity):
    # The authenticated bind has the existing disk budget. The Observer's
    # 128 MiB /tmp still holds the small capture ZIPs, while SQLite's FILE
    # sorter for the large legacy table uses this one-shot disk directory.
    with tempfile.TemporaryDirectory(prefix=".rc6-history-sqlite-", dir=HISTORY.parent) as scratch, \
            bundle.open("rb") as payload:
        mounted = "/app/data/" + Path(scratch).name
        return subprocess.run(
            ["/usr/bin/docker", "--host", "unix:///run/user/1000/porota-rc6-docker.sock",
             "container", "exec", "--interactive", "--workdir", "/app",
             "--env", "SQLITE_TMPDIR=" + mounted, "--env", "TMPDIR=" + mounted, cid,
             "python", "-B", "-c", HISTORY_LOADER, identity, str(HISTORY_LEGACY_ROWS),
             json.dumps(CAPTURE_PINS, separators=(",", ":"))],
            stdin=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=450, env=_rootless_client_environment(account), cwd="/")


def _retire_history_seal(anchor, apply_run):
    # An O_PATH dirfd can resolve unlinkat but cannot be fsynced.
    descriptor = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                         dir_fd=anchor)
    try:
        for name in ("transfer.key", "transfer.crt", "sealed-" + apply_run + ".cms"):
            os.unlink(name, dir_fd=descriptor)
        os.fsync(descriptor)
    except OSError as exc:
        raise Hold("HISTORY_IMPORT_TRANSFER_CLEANUP_INCOMPLETE") from exc
    finally:
        os.close(descriptor)


def apply_reference_history(prepare_run, apply_run, capsule_digest, release):
    account = _operator_identity_guard()
    if not RUN_ID.fullmatch(apply_run) or not HEX64.fullmatch(capsule_digest):
        raise Hold("HISTORY_IMPORT_CAPSULE_INVALID")
    folder = _history_folder(prepare_run)
    if not (folder / "transfer.key").is_file() or (folder / "transfer.key").is_symlink():
        raise Hold("HISTORY_IMPORT_KEY_UNAVAILABLE")
    fd = _anchor_directory(folder)
    try:
        manifest, _ = _open_private_restore_member(fd, "reference-apply.json", 8192)
        sealed, _ = _open_private_restore_member(fd, "sealed-" + apply_run + ".cms", CAPSULE_MAX)
        with manifest, sealed:
            capture = json.load(manifest)
            if tuple(capture.get("release", ())) != release:
                raise Hold("HISTORY_IMPORT_RELEASE_INVALID")
            if hashlib.file_digest(sealed, "sha256").hexdigest() != capsule_digest:
                raise Hold("HISTORY_IMPORT_CAPSULE_INVALID")
            sealed.seek(0)
            with _private_umask(), tempfile.TemporaryDirectory(prefix="apply-", dir=folder) as scratch:
                bundle = Path(scratch) / "reference.tar"
                with bundle.open("xb") as out:
                    subprocess.run(
                        ["/usr/bin/openssl", "cms", "-decrypt", "-binary", "-inform", "DER",
                         "-recip", str(folder / "transfer.crt"), "-inkey", str(folder / "transfer.key")],
                        stdin=sealed, stdout=out, stderr=subprocess.DEVNULL, check=True, timeout=60)
                if not 0 < bundle.stat().st_size <= CAPSULE_MAX:
                    raise Hold("HISTORY_IMPORT_CAPSULE_INVALID")
                deadline = time.monotonic() + 165
                with _history_eod_lock(), \
                        _verified_restore(CUSTODY, "HISTORY", prepare_run, deadline,
                                          maximum=MIGRATION_MAX_BYTES,
                                          expected_sha=capture["restore_sha256"]), \
                        _anchored_source_sqlite(OBSERVER) as paper:
                    marker = _release_marker(MARKER, release)
                    _rootless_runtime_binding(marker, HISTORY, release=release)
                    _live_paper_zero(paper)
                    proof = _exit_quiescence(paper)
                    if _history_source_pin() != capture["source"]:
                        raise Hold("HISTORY_IMPORT_SOURCE_CHANGED")
                    _, current = _rootless_current_state()
                    cid = current["roles"]["observer"]
                    if not HEX64.fullmatch(cid) or current["source_sha"] != release[0]:
                        raise Hold("RUNTIME_DB_BINDING_NO_VERIFICADO")
                    identity = ":".join(map(str, capture["source"]["db"][:2]))
                    result = _execute_history_loader(account, cid, bundle, identity)
                    if result.returncode:
                        raise Hold(_history_loader_failure(result.returncode, result.stderr))
                    if len(result.stdout) > 8192:
                        raise Hold("HISTORY_IMPORT_RECEIPT_INVALID")
                    observed = json.loads(result.stdout)
                    _rootless_runtime_binding(_release_marker(MARKER, release), HISTORY, release=release)
                    _live_paper_zero(paper)
                    if _exit_quiescence(paper) != proof:
                        raise Hold("EXIT_QUIESCENCE_UNVERIFIED")
        receipt = history_reference_receipt(
            {"schema": "rc6.history-reference-transfer.v1", "state": "HISTORY_REFERENCE_APPLIED",
             "run_id": apply_run, "restore_sha256": capture["restore_sha256"], **observed},
            "HISTORY_REFERENCE_APPLY", 0)
        if receipt["state"] != "HISTORY_REFERENCE_APPLIED":
            raise Hold("HISTORY_IMPORT_RECEIPT_INVALID")
        _retire_history_seal(fd, apply_run)
        return receipt
    finally:
        os.close(fd)


def _read_history_reference_totals(db):
    db.execute("PRAGMA query_only=ON")
    db.execute("BEGIN")
    try:
        expected = {digest: count for digest, count in CAPTURE_PINS.values()}
        for table in ("history_versions_v2", "history_canonical_v2"):
            rows = db.execute(f"""
                SELECT json_extract(metadata_json, '$.archive_sha256'), COUNT(*),
                    SUM(CASE WHEN source='IOL_MCP' AND price_basis='SOURCE_REPORTED'
                        AND currency='UNKNOWN' AND settlement='UNKNOWN' THEN 1 ELSE 0 END)
                FROM {table}
                WHERE json_extract(metadata_json, '$.archive_sha256') IN (?, ?)
                GROUP BY 1
            """, tuple(expected)).fetchall()
            if {digest: total for digest, total, reported in rows if total == reported} != expected:
                raise Hold("HISTORY_REFERENCE_COVERAGE_NOT_VERIFIED")
        legacy = db.execute("SELECT COUNT(*) FROM history_versions_v2_legacy").fetchone()[0]
        canonical = db.execute("SELECT COUNT(*) FROM history_canonical_v2_legacy").fetchone()[0]
        if legacy != HISTORY_LEGACY_ROWS or canonical != HISTORY_LEGACY_CANONICAL:
            raise Hold("HISTORY_IMPORT_LEGACY_COUNT_CHANGED")
        if (db.execute("PRAGMA quick_check").fetchone() != ("ok",)
                or db.execute("PRAGMA foreign_key_check").fetchone() is not None):
            raise Hold("HISTORY_REFERENCE_SQLITE_CHECK_FAILED")
        return {"source_reported_committed_rows": sum(expected.values()),
                "legacy_versions_preserved": legacy, "legacy_canonical_preserved": canonical,
                "quick_check": "ok", "source_sql_writes": 0}
    finally:
        db.execute("ROLLBACK")


def verify_reference_history(prepare_run, verify_run, release):
    _operator_identity_guard()
    if not RUN_ID.fullmatch(verify_run):
        raise Hold("HISTORY_IMPORT_RUN_ID_INVALID")
    folder = _history_folder(prepare_run)
    fd = _anchor_directory(folder)
    try:
        saved, _ = _open_private_restore_member(fd, "reference-apply.json", 8192)
        with saved:
            capture = json.load(saved)
        seal_retired = not any(os.path.lexists(folder / name) for name in ("transfer.key", "transfer.crt"))
    finally:
        os.close(fd)
    if tuple(capture.get("release", ())) != release:
        raise Hold("HISTORY_IMPORT_RELEASE_INVALID")
    expected = capture.get("restore_sha256", "")
    before = _history_source_pin()
    if before["db"][:2] != capture["source"]["db"][:2]:
        raise Hold("HISTORY_IMPORT_INODE_CHANGED")
    deadline = time.monotonic() + 320
    with _verified_restore(CUSTODY, "HISTORY", prepare_run, deadline,
                           maximum=MIGRATION_MAX_BYTES, expected_sha=expected) as (_, digest, _), \
            _anchored_source_sqlite(OBSERVER) as paper, _anchored_source_sqlite(HISTORY) as source:
        _rootless_runtime_binding(_release_marker(MARKER, release), HISTORY, release=release)
        _live_paper_zero(paper)
        source.set_progress_handler(lambda: int(time.monotonic() > deadline), 5000)
        try:
            evidence = _read_history_reference_totals(source)
        except sqlite3.Error as exc:
            raise Hold("HISTORY_REFERENCE_LIVE_SQL_UNVERIFIED") from exc
        _live_paper_zero(paper)
    if _history_source_pin() != before:
        raise Hold("HISTORY_IMPORT_SOURCE_CHANGED")
    return {"schema": "rc6.history-reference-transfer.v1", "state": "HISTORY_REFERENCE_VERIFIED",
            "run_id": verify_run, "restore_sha256": digest, "seal_retired": seal_retired,
            "real_orders_sent": 0, **evidence}


def history_reference_receipt(raw, mode, rc):
    schema = "rc6.history-reference-transfer.v1"
    expected = {"HISTORY_REFERENCE_PREPARE": "HISTORY_REFERENCE_READY",
                "HISTORY_REFERENCE_APPLY": "HISTORY_REFERENCE_APPLIED",
                HISTORY_VERIFY: "HISTORY_REFERENCE_VERIFIED"}
    failed = {"schema": schema, "state": "NO_VERIFICADO"}
    if not isinstance(raw, dict) or rc or raw.get("schema") != schema or raw.get("state") != expected.get(mode) \
            or not RUN_ID.fullmatch(str(raw.get("run_id", ""))) or not HEX64.fullmatch(str(raw.get("restore_sha256", ""))):
        reason = str(raw.get("failure_reason", "")) if isinstance(raw, dict) else ""
        if re.fullmatch(r"(?:HISTORY|RUNTIME|CUSTODY|PRIVATE|SOURCE|PAPER|EXIT|SQLITE|RESTORED)_[A-Z0-9_]{3,90}", reason):
            failed["failure_reason"] = reason
        return failed
    core = {key: raw[key] for key in ("schema", "state", "run_id", "restore_sha256")}
    if mode == "HISTORY_REFERENCE_PREPARE":
        cert = raw.get("certificate_b64")
        if not isinstance(cert, str) or not 100 <= len(cert) <= 4096 or not re.fullmatch(r"[A-Za-z0-9+/=]+", cert):
            return failed
        return core | {"certificate_b64": cert}
    if mode == HISTORY_VERIFY:
        counts = {"source_reported_committed_rows": sum(count for _, count in CAPTURE_PINS.values()),
                  "legacy_versions_preserved": HISTORY_LEGACY_ROWS, "legacy_canonical_preserved": HISTORY_LEGACY_CANONICAL,
                  "source_sql_writes": 0, "real_orders_sent": 0}
        if (any(type(raw.get(k)) is not int or raw[k] != v for k, v in counts.items())
                or raw.get("quick_check") != "ok" or type(raw.get("seal_retired")) is not bool):
            return failed
        return core | counts | {"quick_check": "ok", "seal_retired": raw["seal_retired"]}
    if (raw.get("source_reported_committed_rows") != 152052 or raw.get("legacy_versions_preserved") != HISTORY_LEGACY_ROWS
            or raw.get("quick_check") != "ok" or raw.get("preparation") not in {"SCHEMA_UPGRADED", "SCHEMA_ALREADY_UPGRADED"}):
        return failed
    changes = ("canonical_updates", "versions_appended")
    if any(type(raw.get(k)) is not int or not 0 <= raw[k] <= 152052 for k in changes):
        return failed
    return core | {key: raw[key] for key in (*changes, "source_reported_committed_rows",
                                             "legacy_versions_preserved", "quick_check", "preparation")}


def fetch_sealed_history(tree, expected, destination):
    if (not re.fullmatch(r"[0-9a-f]{40}", tree) or not HEX64.fullmatch(expected)
            or not os.environ.get("GH_TOKEN")):
        raise Hold("HISTORY_IMPORT_CAPSULE_INVALID")
    api = "https://api.github.com/repos/" + OWNER_BRANCH + "/git/"

    def fetch(path, maximum):
        request = urllib.request.Request(api + path, headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"],
            "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read(maximum + 1)
        if len(raw) > maximum:
            raise Hold("HISTORY_IMPORT_CAPSULE_INVALID")
        return json.loads(raw)

    index = fetch("trees/" + tree, 32768)
    entries = index.get("tree")
    if index.get("sha") != tree or index.get("truncated") is not False or not isinstance(entries, list) \
            or not 1 <= len(entries) <= CAPSULE_MAX // CAPSULE_PART:
        raise Hold("HISTORY_IMPORT_CAPSULE_INVALID")
    digest = hashlib.sha256()
    created = False
    try:
        with _private_umask(), destination.open("xb") as output:
            created = True
            for number, entry in enumerate(entries):
                sha, declared = entry.get("sha", ""), entry.get("size")
                if (entry.get("path") != f"part-{number:03d}.der" or entry.get("type") != "blob"
                        or entry.get("mode") != "100644" or not re.fullmatch(r"[0-9a-f]{40}", sha)
                        or type(declared) is not int or not 1 <= declared <= CAPSULE_PART
                        or (number != len(entries)-1 and declared != CAPSULE_PART)):
                    raise Hold("HISTORY_IMPORT_CAPSULE_INVALID")
                blob = fetch("blobs/" + sha, CAPSULE_PART * 2)
                if blob.get("sha") != sha or blob.get("encoding") != "base64":
                    raise Hold("HISTORY_IMPORT_CAPSULE_INVALID")
                part = base64.b64decode("".join(blob["content"].splitlines()), validate=True)
                if len(part) != declared:
                    raise Hold("HISTORY_IMPORT_CAPSULE_INVALID")
                output.write(part)
                digest.update(part)
        if digest.hexdigest() != expected:
            raise Hold("HISTORY_IMPORT_CAPSULE_DIGEST_MISMATCH")
    except BaseException:
        if created:
            destination.unlink()
        raise



HISTORY_UPGRADE_REHEARSAL = "RESTORED_HISTORY_UPGRADE_REHEARSAL"
HISTORY_UPGRADE_SCHEMA = "rc6.restored-history-upgrade-rehearsal.v1"
REHEARSE_UPGRADE = """
from pathlib import Path
import json
import sys
from cu_history_store_v2_hf6 import upgrade_existing_history
from cv_history_store_adapter_hf6 import HistoricalStore

target = Path(sys.argv[1])
info = target.stat()
store = HistoricalStore(str(target), source_reported_only=True,
                        existing_history_identity=(info.st_dev, info.st_ino))
result = upgrade_existing_history(store, expected_versions=int(sys.argv[2]), seconds=510)
assert (target.stat().st_dev,target.stat().st_ino)==(info.st_dev,info.st_ino)
retry = upgrade_existing_history(store, expected_versions=int(sys.argv[2]), seconds=120)
assert retry['state']=='SCHEMA_ALREADY_UPGRADED'
with store.connect() as c:
    names = {row[0] for row in c.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
    result['audit_tables_permanent'] = sum('migration_' in name for name in names)
    result['exact_canonical_rows'] = c.execute(
         "SELECT COUNT(*) FROM history_canonical_v2 WHERE price_basis='RAW'").fetchone()[0]
print(json.dumps(result))
"""


def rehearse_existing_upgrade(database, product, history_run, expected_sha):
    database = Path(database)
    runner = Path(os.environ["RUNNER_TEMP"]).resolve()
    if (os.environ.get("GITHUB_ACTIONS") != "true" or database.is_symlink()
            or not database.resolve().is_relative_to(runner)
            or not RUN_ID.fullmatch(history_run) or not HEX64.fullmatch(expected_sha)
            or not 4096 <= database.stat().st_size <= MIGRATION_MAX_BYTES
            or _sha256(database) != expected_sha):
        raise Hold("OFFHOST_RESTORE_BINDING_UNVERIFIED")
    with tempfile.TemporaryDirectory(prefix="history-upgrade-", dir=runner) as scratch:
        target = Path(scratch) / "market_history.db"
        shutil.copyfile(database, target)
        task = subprocess.run([sys.executable, "-B", "-c", REHEARSE_UPGRADE,
                               str(target), str(HISTORY_LEGACY_ROWS)],
                              cwd=product, capture_output=True, timeout=650, check=False)
        if task.returncode:
            cause = re.search(rb'\b(HISTORY_[A-Z0-9_]{3,85})\s*$', task.stderr.strip())
            raise Hold(cause[1].decode() if cause else "OFFHOST_HISTORY_UPGRADE_FAILED")
        if len(task.stdout) > 8192:
            raise Hold("OFFHOST_HISTORY_UPGRADE_FAILED")
        result = json.loads(task.stdout)
    if (result.get("state") != "SCHEMA_UPGRADED" or result.get("source_rows") != HISTORY_LEGACY_ROWS
            or result.get("migrated_rows") != 0 or result.get("quarantined_rows") != HISTORY_LEGACY_ROWS
            or result.get("audit_tables_permanent") != 0 or result.get("exact_canonical_rows") != 0
            or _sha256(database) != expected_sha):
        raise Hold("OFFHOST_HISTORY_UPGRADE_ACCOUNTING_FAILED")
    return {"schema": HISTORY_UPGRADE_SCHEMA, "state": "RESTORED_HISTORY_UPGRADE_REHEARSED",
            "history_run": history_run, "source_sha256": expected_sha, "legacy_versions": HISTORY_LEGACY_ROWS,
            "new_exact_rows": 0, "permanent_migration_audit_tables": 0,
            "legacy_canonical_rows": result["legacy_canonical_rows"], "source_sql_writes": 0}


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    try:
        if arguments[:1] == ["host-history-prepare"] and len(arguments) == 5:
            print(json.dumps(prepare_reference_history(arguments[1], _history_release(*arguments[2:]))))
            return 0
        if arguments[:1] == ["host-history-apply"] and len(arguments) == 7:
            print(json.dumps(apply_reference_history(*arguments[1:4], _history_release(*arguments[4:]))))
            return 0
        if arguments[:1] == ["host-history-verify"] and len(arguments) == 6:
            print(json.dumps(verify_reference_history(*arguments[1:3], _history_release(*arguments[3:]))))
            return 0
        if arguments[:1] == ["fetch-sealed-history"] and len(arguments) == 4:
            fetch_sealed_history(arguments[1], arguments[2], Path(arguments[3]))
            return 0
        if arguments[:1] == ["host-preflight"] and len(arguments) in (1, 2):
            _operator_identity_guard()
            role = arguments[1] if len(arguments) == 2 else "OBSERVER"
            if role not in DATABASES:
                raise Hold("MODE_UNSUPPORTED")
            receipt = host_preflight(DATABASES[role])
            if role == "HISTORY":
                receipt["database_role"] = role
            print(json.dumps(receipt, sort_keys=True))
            return 0
        if arguments[:1] == ["host-custody"] and len(arguments) in (2, 3):
            _operator_identity_guard()
            role = arguments[2] if len(arguments) == 3 else "OBSERVER"
            if role not in DATABASES:
                raise Hold("MODE_UNSUPPORTED")
            paper_db = OBSERVER if role == "HISTORY" else None
            print(json.dumps(host_custody_once(DATABASES[role], run_id=arguments[1],
                                               paper_db_path=paper_db), sort_keys=True))
            return 0
        if arguments[:1] == ["host-restored-inventory"] and len(arguments) == 4:
            _operator_identity_guard()
            print(json.dumps(host_restored_history_inventory(*arguments[1:]), sort_keys=True))
            return 0
        if arguments[:1] == ["host-restored-history-export"] and len(arguments) == 3:
            _operator_identity_guard()
            stream_restored_history(arguments[1], arguments[2], sys.stdout.buffer)
            return 0
        if len(arguments) == 2 and arguments[0] == "verify-owner":
            workstream = verify_owner_from_github(arguments[1],
                database_role=os.environ.get("REQUEST_DATABASE_ROLE", "OBSERVER"),
                host_scope=(HISTORY_WRITE_SCOPE if os.environ.get("REQUEST_MODE") in HISTORY_MODES else None))
            print("DEPLOY_OWNER_VERIFIED=" + workstream)
            return 0
        if len(arguments) == 5 and arguments[0] == "sanitize":
            _, mode, raw_path, public_path, rc_text = arguments
            if not re.fullmatch(r"[0-9]{1,3}", rc_text):
                raise Hold("REMOTE_RC_INVALID")
            raw_file = Path(raw_path)
            if raw_file.is_file() and raw_file.stat().st_size <= 64 * 1024:
                try:
                    raw = json.loads(raw_file.read_text())
                except (OSError, ValueError, TypeError):
                    raw = {}
            else:
                raw = {}
            if mode in HISTORY_MODES or mode == HISTORY_VERIFY:
                public = history_reference_receipt(raw, mode, int(rc_text))
            elif mode == INVENTORY_MODE:
                public = sanitize_restored_inventory(
                    raw, remote_rc=int(rc_text), observer_run=os.getenv("OBSERVER_RESTORE_RUN", ""),
                    history_run=os.getenv("HISTORY_RESTORE_RUN", ""), mask=os.getenv("HISTORY_CALENDAR", ""))
            else:
                public = sanitize_receipt(mode, raw, remote_rc=int(rc_text),
                                          expected_run_id=os.environ.get("GITHUB_RUN_ID"),
                                          database_role=os.environ.get("REQUEST_DATABASE_ROLE", "OBSERVER"))
            with _private_umask(), Path(public_path).open("x", encoding="utf-8") as target:
                json.dump(public, target, sort_keys=True)
            print("RC6_CUSTODY_SANITIZED=" + public["state"])
            return 0
        if len(arguments) == 7 and arguments[0] == "rehearse-existing-upgrade":
            _, database, product, run_id, expected_sha, rc_text, public_path = arguments
            try:
                if rc_text != "0":
                    raise Hold("OFFHOST_RESTORE_TRANSFER_FAILED")
                public = rehearse_existing_upgrade(database, product, run_id, expected_sha)
            except (Hold, OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
                public = {"schema": HISTORY_UPGRADE_SCHEMA, "state": "NO_VERIFICADO",
                          "failure_reason": str(exc) if isinstance(exc, Hold) else "OFFHOST_HISTORY_UPGRADE_FAILED"}
            with _private_umask(), Path(public_path).open("x", encoding="utf-8") as target:
                json.dump(public, target, sort_keys=True)
            return 0
        if len(arguments) == 7 and arguments[0] == "rehearse-restored":
            _, database, product, run_id, expected_sha, rc_text, public_path = arguments
            try:
                if rc_text != "0":
                    raise Hold("OFFHOST_RESTORE_TRANSFER_FAILED")
                public = rehearse_restored_history(Path(database), Path(product), run_id, expected_sha)
            except (Hold, OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired) as exc:
                reason = str(exc) if isinstance(exc, Hold) else "OFFHOST_REHEARSAL_FAILED"
                if isinstance(exc, subprocess.TimeoutExpired):
                    reason = "OFFHOST_REHEARSAL_TIME_BUDGET_EXHAUSTED"
                public = {"schema": MIGRATION_SCHEMA, "state": "NO_VERIFICADO", "failure_reason": reason}
            with _private_umask(), Path(public_path).open("x", encoding="utf-8") as target:
                json.dump(public, target, sort_keys=True)
            print("RC6_CUSTODY_SANITIZED=" + public["state"])
            return 0
        raise Hold("ACTION_UNSUPPORTED")
    except (Hold, OSError, sqlite3.Error, ValueError, TypeError, subprocess.SubprocessError) as exc:
        if arguments[:1] in (["host-history-prepare"], ["host-history-apply"], ["host-history-verify"]):
            print(json.dumps({"schema": "rc6.history-reference-transfer.v1",
                              "failure_reason": str(exc) if isinstance(exc, Hold) else "HISTORY_IMPORT_UNCLASSIFIED"}))
        if arguments[:1] in (["host-preflight"], ["host-custody"], ["host-restored-inventory"]):
            reason = str(exc) if isinstance(exc, Hold) else "CUSTODY_UNCLASSIFIED"
            schema = INVENTORY_SCHEMA if arguments[0] == "host-restored-inventory" else SCHEMA
            failure = {"schema": schema, "failure_reason":
                       reason if reason in PUBLIC_HOLD_REASONS else "CUSTODY_UNCLASSIFIED"}
            if isinstance(exc, InventorySqlHold):
                failure.update(sql_phase=exc.phase, sqlite_errorcode=exc.code)
            print(json.dumps(failure))
        print("RC6_CUSTODY_FAIL_CLOSED", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
