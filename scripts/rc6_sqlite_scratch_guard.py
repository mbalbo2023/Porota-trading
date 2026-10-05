#!/usr/bin/env python3
"""Candidate-native disk admission for private derived SQLite copies.

The probe opens no source with SQLite. Its sizes are admission observations;
the capture helper still verifies source identity and hashes when it copies.
Only the private scratch lease may be created by the existing-root inspector.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import stat
import sys


MAX_BYTES = 512 * 1024**2
RESERVE_BYTES = 2 * 1024**3
MIN_FREE_INODE_PERCENT = 10
OWNER_UID = OWNER_GID = 1000
ENV_PREFIX = "POROTA_SQLITE_SCRATCH_"
ENV_KEYS = tuple(ENV_PREFIX + name for name in
                 ("ROOT", "MAX_BYTES", "RESERVE_BYTES", "MIN_FREE_INODE_PERCENT"))
SCHEMA = "rc6.sqlite-scratch-admission.v1"
HOST_DATA = Path("/opt/porota-trading/data")
PRIMARY_RELATIVE = Path("paper_v17/observer_v17.db")
HISTORY_DEFAULT = "/app/data/market_history.db"


class ScratchAdmissionError(ValueError):
    pass


def runtime_settings(root, environ=None):
    values = dict(zip(ENV_KEYS, (str(root), str(MAX_BYTES), str(RESERVE_BYTES),
                               str(MIN_FREE_INODE_PERCENT))))
    source = {} if environ is None else environ
    for key, value in values.items():
        if key in source and source[key] != value:
            raise ScratchAdmissionError("RC6_SCRATCH_CONFIGURATION_IMMUTABLE:" + key)
    return values


def history_container_path(environ=None):
    raw = ({} if environ is None else environ).get("HIST_DB_PATH", HISTORY_DEFAULT)
    if not isinstance(raw, str) or not re.fullmatch(r"/app/data/[A-Za-z0-9_./-]+", raw):
        raise ScratchAdmissionError("RC6_HISTORY_PATH_OUTSIDE_DATA")
    parsed = PurePosixPath(raw)
    if (parsed.as_posix() != raw or ".." in parsed.parts
            or parsed.suffix not in {".db", ".sqlite", ".sqlite3"}):
        raise ScratchAdmissionError("RC6_HISTORY_PATH_OUTSIDE_DATA")
    return raw


def history_host_path(data_root, container_path):
    container_path = history_container_path({"HIST_DB_PATH": container_path})
    return _canonical(Path(data_root) / PurePosixPath(container_path).relative_to("/app/data"))


def _history_env(repo_root, *, owner_uid=OWNER_UID):
    path = Path(repo_root) / ".env"
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_NONBLOCK)
    except FileNotFoundError:
        return {}
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1024**2
                or info.st_uid not in {0, owner_uid} or stat.S_IMODE(info.st_mode) & 0o7133):
            raise ScratchAdmissionError("RC6_HISTORY_CONFIG_CUSTODY_REQUIRED")
        result = {}
        raw = handle.read(1024**2 + 1)
        after = os.fstat(handle.fileno())
        if (len(raw) != info.st_size or (info.st_dev, info.st_ino, info.st_size,
                info.st_mtime_ns, info.st_ctime_ns) != (after.st_dev, after.st_ino,
                after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise ScratchAdmissionError("RC6_HISTORY_CONFIG_CHANGED")
        for line in raw.decode("utf-8").splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key, value = line.split("=", 1)
            if key.strip() != "HIST_DB_PATH":
                continue
            if key.strip() in result:
                raise ScratchAdmissionError("RC6_HISTORY_CONFIG_DUPLICATE")
            result[key.strip()] = value.strip().strip('"').strip("'")
        return result


def validate_policy(policy):
    expected = {"root_suffix": "sqlite-read-scratch", "max_bytes": MAX_BYTES,
                "reserve_bytes": RESERVE_BYTES,
                "min_free_inode_percent": MIN_FREE_INODE_PERCENT,
                "owner_uid": OWNER_UID, "owner_gid": OWNER_GID,
                "mode": "0700", "lease": "NONBLOCKING_SINGLE_READER",
                "crash_residue": "PRESERVE_IDENTIFIED_COUNT_TOWARD_QUOTA"}
    actual = policy.get("sqlite", {}).get("private_read_scratch")
    if (not isinstance(actual, dict) or actual != expected
            or any(type(actual.get(key)) is not type(value) for key, value in expected.items())):
        raise ScratchAdmissionError("RC6_SCRATCH_POLICY_DRIFT")
    return expected


def _api():
    supplied = globals().get("_CANDIDATE_SCRATCH_API")
    if supplied is not None:
        return supplied
    if str(Path(__file__).resolve().parents[1]) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from rc6_audit_evidence import sqlite_scratch
    return vars(sqlite_scratch)


def disk_backed_type(path):
    return _api()["_storage_type"](_canonical(path))


def _canonical(path):
    value = Path(path)
    if (not value.is_absolute() or value != value.absolute() or ".." in value.parts
            or str(value) != str(path) or value.resolve() != value):
        raise ScratchAdmissionError("RC6_SCRATCH_PATH_ALIAS")
    for member in (value, *value.parents):
        try:
            info = member.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise ScratchAdmissionError("RC6_SCRATCH_PATH_ALIAS")
    return value


def _existing_parent(path):
    for parent in (path, *path.parents):
        if parent.exists():
            if not parent.is_dir():
                raise ScratchAdmissionError("RC6_SCRATCH_PARENT_INVALID")
            return parent
    raise ScratchAdmissionError("RC6_SCRATCH_PARENT_INVALID")


def source_sizes(path, *, owner_uid):
    path = _canonical(path)
    sizes = {}
    for suffix in ("", "-wal", "-shm", "-journal"):
        member = Path(str(path) + suffix)
        try:
            info = member.lstat()
        except FileNotFoundError:
            if not suffix:
                raise ScratchAdmissionError("RC6_SCRATCH_SOURCE_UNAVAILABLE") from None
            continue
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != owner_uid or stat.S_IMODE(info.st_mode) & 0o7133):
            raise ScratchAdmissionError("RC6_SCRATCH_SOURCE_CUSTODY_REQUIRED")
        sizes[suffix] = info.st_size
    if sizes.get("-journal", 0):
        raise ScratchAdmissionError("RC6_SCRATCH_SOURCE_BUSY")
    if sizes[""] <= 0 or sum(sizes.values()) > MAX_BYTES:
        raise ScratchAdmissionError("RC6_SCRATCH_SOURCE_BYTE_LIMIT")
    return sizes


def _drop_to_owner(owner_uid, owner_gid):
    if os.geteuid() == 0:
        os.setgroups([])
        os.setgid(owner_gid)
        os.setuid(owner_uid)
    if os.geteuid() != owner_uid or os.getegid() != owner_gid:
        raise ScratchAdmissionError("RC6_SCRATCH_OWNER_REQUIRED")


def probe(database, data_root, *, owner_uid=OWNER_UID, owner_gid=OWNER_GID,
          allow_empty_primary=False, history_container=None):
    database, data_root = _canonical(database), _canonical(data_root)
    if database != data_root / PRIMARY_RELATIVE:
        raise ScratchAdmissionError("RC6_SCRATCH_PRIMARY_DATASET_REQUIRED")
    root = _canonical(database.parent / "artifacts" / database.name / "sqlite-read-scratch")
    if root.exists():
        info = root.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != owner_uid
                or info.st_gid != owner_gid or stat.S_IMODE(info.st_mode) != 0o700):
            raise ScratchAdmissionError("RC6_SCRATCH_ROOT_CUSTODY_REQUIRED")
    api = _api()
    if (api["MAX_BYTES"] != MAX_BYTES or api["RESERVE_BYTES"] != RESERVE_BYTES
            or api["MIN_FREE_INODE_PERCENT"] != MIN_FREE_INODE_PERCENT):
        raise ScratchAdmissionError("RC6_SCRATCH_HELPER_LIMIT_DRIFT")
    sources = {}
    if database.exists() or database.is_symlink() or not allow_empty_primary:
        sources["primary"] = source_sizes(database, owner_uid=owner_uid)
    selected_history = history_container_path(_history_env(data_root.parent, owner_uid=owner_uid))
    if history_container is not None:
        selected_history = history_container_path({"HIST_DB_PATH": history_container})
    history = history_host_path(data_root, selected_history)
    if history.exists() or history.is_symlink():
        sources["history_selected"] = source_sizes(history, owner_uid=owner_uid)
    elif selected_history != HISTORY_DEFAULT:
        raise ScratchAdmissionError("RC6_HISTORY_NEED_COPY_TARGET")
    _drop_to_owner(owner_uid, owner_gid)
    nearest = _existing_parent(root)
    filesystem = api["_storage_type"](nearest)
    filesystem_stats = os.statvfs(nearest)
    if root.exists():
        observed = api["inspect_scratch"](root, max_bytes=MAX_BYTES,
            reserve_bytes=0, min_free_inode_percent=0)
        # Inspection observes low-space metadata so safe cleanup can be tried.
        # Admission below independently enforces the full runtime reserve.
        occupied = observed["occupied_bytes"]
        free = observed["free_bytes"]
        free_inodes, total_inodes = observed["free_inodes"], observed["total_inodes"]
        sessions = observed["residual_sessions"]
    else:
        occupied, sessions = 0, 0
        free = filesystem_stats.f_bavail * filesystem_stats.f_frsize
        free_inodes, total_inodes = filesystem_stats.f_favail, filesystem_stats.f_files
    peaks = {name: api["snapshot_peak_bytes"](sizes[""], sizes.get("-wal", 0),
             sizes.get("-shm", 0), allocation_unit=filesystem_stats.f_frsize)
             for name, sizes in sources.items()}
    if type(occupied) is not int or not 0 <= occupied < MAX_BYTES:
        raise ScratchAdmissionError("RC6_SCRATCH_RESIDUE_BYTE_LIMIT")
    if any(occupied + peak > MAX_BYTES for peak in peaks.values()):
        raise ScratchAdmissionError("RC6_SCRATCH_CAPTURE_PEAK_BYTE_LIMIT")
    growth = MAX_BYTES - occupied
    inode_percent = 100.0 * free_inodes / total_inodes if total_inodes > 0 else 0.0
    safe = free >= RESERVE_BYTES + growth and inode_percent >= MIN_FREE_INODE_PERCENT
    return {"schema": SCHEMA, "status": "GREEN" if safe else "RED",
            "verification": "NATIVE_FILESYSTEM_METADATA_ADMISSION_NOT_SOURCE_SNAPSHOT",
            "primary_dataset": str(database), "scratch_root": str(root),
            "history_container": selected_history, "history_host": str(history),
            "filesystem": filesystem, "source_sizes": sources,
            "capture_peak_bytes": peaks, "occupied_bytes": occupied,
            "scratch_growth_bytes": growth, "max_bytes": MAX_BYTES,
            "reserve_bytes": RESERVE_BYTES, "free_bytes": free,
            "free_inode_percent": inode_percent, "residual_sessions": sessions,
            "lease_owner_uid": owner_uid, "lease_owner_gid": owner_gid,
            "source_sqlite_opened": False, "residue_deleted": False}


def emit_probe(repo_root):
    repo_root = Path(repo_root)
    helper = (repo_root / "rc6_audit_evidence/sqlite_scratch.py").read_bytes()
    guard = Path(__file__).read_bytes()
    if len(helper) > 1024**2 or len(guard) > 1024**2:
        raise ScratchAdmissionError("RC6_SCRATCH_PROBE_SOURCE_LIMIT")
    payload = base64.b64encode(json.dumps({"helper": helper.decode("utf-8"),
         "guard": guard.decode("utf-8")}, ensure_ascii=True).encode("utf-8")).decode("ascii")
    # Payload carries reviewed source only. It carries no environment or token.
    return ("import base64,json\n" + "p=json.loads(base64.b64decode(" + repr(payload) + "))\n"
        "h={'__name__':'rc6_candidate_sqlite_scratch'}\n"
        "exec(compile(p['helper'],'<frozen-scratch-helper>','exec'),h)\n"
        "g={'__name__':'__main__','_CANDIDATE_SCRATCH_API':h}\n"
        "exec(compile(p['guard'],'<frozen-scratch-admission>','exec'),g)\n")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--emit-probe", action="store_true")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--database", type=Path, default=HOST_DATA / PRIMARY_RELATIVE)
    parser.add_argument("--data-root", type=Path, default=HOST_DATA)
    parser.add_argument("--owner-uid", type=int, default=OWNER_UID)
    parser.add_argument("--owner-gid", type=int, default=OWNER_GID)
    parser.add_argument("--allow-empty-primary", action="store_true")
    parser.add_argument("--history-container")
    args = parser.parse_args(argv)
    try:
        if args.emit_probe:
            print(emit_probe(args.repo_root), end="")
            return 0
        report = probe(args.database, args.data_root,
                       owner_uid=args.owner_uid, owner_gid=args.owner_gid,
                       allow_empty_primary=args.allow_empty_primary,
                       history_container=args.history_container)
        print(json.dumps(report, sort_keys=True, allow_nan=False))
        return 0 if report["status"] == "GREEN" else 42
    except (OSError, ValueError, KeyError, TypeError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) and str(exc).replace("_", "").isalnum() else "RC6_SCRATCH_PROBE_FAILED"
        print(json.dumps({"schema": SCHEMA, "status": "RED", "reason": reason}))
        return 42


if __name__ == "__main__":
    raise SystemExit(main())
