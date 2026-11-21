#!/usr/bin/env python3
"""RC6 OBSERVER one-shot online custody. Never run from an untrusted ref."""

from __future__ import annotations

from contextlib import ExitStack, closing, contextmanager
from datetime import datetime, timezone
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
MAX_DB_BYTES = 5 * 1024**3
MAX_WAL_BYTES = 512 * 1024**2
MIN_RESERVE_BYTES = 2 * 1024**3
RUN_ID = re.compile(r"[1-9][0-9]{7,14}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
OWNER_BRANCH = "mbalbo2023/Porota-trading"
OWNER_ISSUE = f"https://api.github.com/repos/{OWNER_BRANCH}/issues/980"
OWNER_PUBLIC = f"https://github.com/{OWNER_BRANCH}/issues/980#issuecomment-"
BACKUP_TIMEOUT_SECONDS = 600
PUBLIC_HOLD_REASONS = frozenset({
    "CUSTODY_BACKUP_TIMEOUT", "CUSTODY_DIRECTORY_UNSAFE", "CUSTODY_DISK_RESERVE_BLOCKED",
    "CUSTODY_DURABILITY_UNVERIFIED", "CUSTODY_LOCK_UNSAFE", "CUSTODY_OWNER_BUSY",
    "CUSTODY_RUN_ALREADY_EXISTS", "CUSTODY_SQLITE_UNAVAILABLE", "CUSTODY_UNCLASSIFIED",
    "EXIT_QUIESCENCE_UNVERIFIED", "PAPER_ORDERS_UNVERIFIED",
    "PARENT_PATH_UNAVAILABLE", "PARENT_PATH_UNSAFE", "PRIVILEGED_PATH_BINDING_UNVERIFIED",
    "PRIVATE_HISTORY_RESTORE_SCHEMA_UNVERIFIED", "PRIVATE_RESTORE_DIGEST_MISMATCH",
    "PRIVATE_RESTORE_QUICK_CHECK_FAILED", "RELEASE_PAPER_IDENTITY_UNVERIFIED",
    "ROOTLESS_SSH_IDENTITY_REQUIRED", "RUNTIME_DB_BINDING_NO_VERIFICADO",
    "ROOTLESS_STATE_PARENT_UNSAFE", "ROOTLESS_STATE_PRIVATE_GROUP_UNVERIFIED",
    "SOURCE_FILE_UNAVAILABLE", "SOURCE_FILE_UNSAFE", "SOURCE_IDENTITY_CHANGED",
    "SQLITE_SIDECAR_UNSAFE",
})


class Hold(Exception):
    """Fail-closed expected condition; never publish the triggering raw path."""


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


def _release_marker(path: Path) -> dict:
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
            or marker.get("candidate_sha") != EXPECTED_SOURCE_SHA
            or marker.get("candidate_tree_sha") != EXPECTED_TREE_SHA
            or marker.get("image_id") != EXPECTED_IMAGE_ID
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
                   custody_root: Path = CUSTODY) -> dict:
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
    _release_marker(marker_path)
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
        home = Path(account.pw_dir)
        env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C",
               "HOME": str(home), "USER": "porotaadmin", "LOGNAME": "porotaadmin",
               "XDG_RUNTIME_DIR": "/run/user/1000",
               "DOCKER_CONFIG": str(home / ".config/porota-rc6/client"),
               "DOCKER_CLI_HINTS": "false"}
        result = subprocess.run([
            "/usr/bin/docker",
            "--host", "unix:///run/user/1000/porota-rc6-docker.sock", *args],
            check=False, input=b"", stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=9, env=env, cwd="/")
        if result.returncode != 0 or len(result.stdout) > 131072:
            raise Hold("RUNTIME_DB_BINDING_NO_VERIFICADO")
        return json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError) as exc:
        raise Hold("RUNTIME_DB_BINDING_NO_VERIFICADO") from exc


def _verify_rootless_attestation(current: dict, daemon: dict, containers: dict,
                                 marker: dict, db_path: Path, *,
                                 data_root: Path = ROOT,
                                 operator_home: Path = Path("/home/porotaadmin")) -> str:
    """Cross-bind marker ↔ private CIDs ↔ inspect ↔ exact host data inode."""
    fail = "RUNTIME_DB_BINDING_NO_VERIFICADO"
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
            or current.get("source_sha") != EXPECTED_SOURCE_SHA
            or current.get("source_tree") != EXPECTED_TREE_SHA
            or current.get("image_id") != EXPECTED_IMAGE_ID
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
                or item.get("Image") != EXPECTED_IMAGE_ID
                or config.get("Image") != EXPECTED_IMAGE_ID
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
        required = {"POROTA_BUILD_SHA": EXPECTED_SOURCE_SHA,
                    "POROTA_CANDIDATE_TREE_SHA": EXPECTED_TREE_SHA,
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


def _rootless_runtime_binding(marker: dict, db_path: Path) -> str:
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
                                              db_path, operator_home=Path(account.pw_dir))
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
                      paper_db_path: Path | None = None) -> dict:
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
    before = host_preflight(db_path, marker_path, custody_root)
    runtime_binding = _rootless_runtime_binding(_release_marker(marker_path), db_path)
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
                    with closing(sqlite3.connect(snapshot, timeout=1)) as target:
                        def progress(status, remaining, total):
                            if time.monotonic() > deadline:
                                raise Hold("CUSTODY_BACKUP_TIMEOUT")
                        source.backup(target, pages=256, sleep=0.1, progress=progress)
                    os.chmod("snapshot.sqlite", 0o600, dir_fd=part_fd,
                             follow_symlinks=False)
                    _fsync_private_file(part_fd, "snapshot.sqlite")
                    _live_paper_zero(paper)
                    if _exit_quiescence(paper) != exit_quiescence:
                        raise Hold("EXIT_QUIESCENCE_UNVERIFIED")
                    if (_rootless_runtime_binding(_release_marker(marker_path), db_path)
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
                    _release_marker(marker_path)  # Same pinned release after copy.
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


def validate_deploy_owner(receipt: dict, comments: list[dict], *, database_role="OBSERVER") -> str:
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
            or _field(body, "Host scope") != f"RC6_{database_role}_ONLINE_CUSTODY_ONCE"):
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


def verify_owner_from_github(receipt_id: str, *, database_role="OBSERVER") -> str:
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
    return validate_deploy_owner(receipt, comments, database_role=database_role)


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    try:
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
        if len(arguments) == 2 and arguments[0] == "verify-owner":
            workstream = verify_owner_from_github(arguments[1],
                database_role=os.environ.get("REQUEST_DATABASE_ROLE", "OBSERVER"))
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
            public = sanitize_receipt(mode, raw, remote_rc=int(rc_text),
                                      expected_run_id=os.environ.get("GITHUB_RUN_ID"),
                                      database_role=os.environ.get("REQUEST_DATABASE_ROLE", "OBSERVER"))
            with _private_umask(), Path(public_path).open("x", encoding="utf-8") as target:
                json.dump(public, target, sort_keys=True)
            print("RC6_CUSTODY_SANITIZED=" + public["state"])
            return 0
        raise Hold("ACTION_UNSUPPORTED")
    except (Hold, OSError, sqlite3.Error, ValueError, TypeError) as exc:
        if arguments[:1] in (["host-preflight"], ["host-custody"]):
            reason = str(exc) if isinstance(exc, Hold) else "CUSTODY_UNCLASSIFIED"
            print(json.dumps({"schema": SCHEMA, "failure_reason":
                              reason if reason in PUBLIC_HOLD_REASONS else "CUSTODY_UNCLASSIFIED"}))
        print("RC6_CUSTODY_FAIL_CLOSED", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
