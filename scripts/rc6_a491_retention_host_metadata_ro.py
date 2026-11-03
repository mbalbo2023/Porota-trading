#!/usr/bin/env python3
"""A491: fixed-role retention metadata probe; no SQLite or market-data reads.

Host mode uses only no-follow metadata syscalls, /proc/meminfo and fixed
systemctl queries. Runner mode reconstructs an allowlisted public receipt.
Neither mode can authorize an A330 snapshot, PAPER admission, or any purge.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time


SCHEMA = "rc6.a491.retention-host-metadata.v1"
DATA = Path("/opt/porota-trading/data")
DATABASES = (
    # A330 private capture uses exactly this 1 GiB source limit. A491 must
    # not report a larger history source as regular/eligible metadata.
    ("HISTORY_DB", DATA / "market_history.db", 1024**3),
    ("PAPER_OBSERVER_DB", DATA / "paper_v17/observer_v17.db", 4 * 1024**3),
)
SIDECARS = ("WAL", "SHM", "JOURNAL")
SUFFIX = {"WAL": "-wal", "SHM": "-shm", "JOURNAL": "-journal"}
UNITS = (
    ("A3_DAILY_TIMER", "porota-a3-history-daily-rc6.timer"),
    ("A3_RECONCILE_TIMER", "porota-a3-history-reconcile-rc6.timer"),
    ("A3_WEEKEND_TIMER", "porota-a3-history-weekend-rc6.timer"),
    ("EOD_WRITER_SERVICE", "porota-local-eod-history-rc6.service"),
    ("POSTCLOSE_WRITER_SERVICE", "porota-history-postclose-rc6.service"),
)
MIN_MEMORY = 512 * 1024**2
MIN_DISK = 4 * 1024**3
MAX_NUMBER = 2**60
MAX_RAW = 8192
FILE_STATES = frozenset({"PRESENT_REGULAR", "MISSING", "UNSAFE_PATH",
                         "UNSAFE_FILE", "SIZE_LIMIT", "UNAVAILABLE",
                         "CHANGED_DURING_PROBE"})
SIDECAR_STATES = frozenset({"PRESENT", "MISSING", "UNSAFE_PATH",
                            "UNSAFE_FILE", "UNAVAILABLE", "CHANGED_DURING_PROBE"})
LOAD = frozenset({"loaded", "not-found", "masked", "error", "UNKNOWN"})
ACTIVE = frozenset({"active", "inactive", "failed", "activating",
                    "deactivating", "reloading", "UNKNOWN"})
UNIT_FILE = frozenset({"enabled", "disabled", "masked", "static", "indirect",
                       "generated", "linked", "alias", "transient", "bad", "UNKNOWN"})
RESULT = frozenset({"success", "exit-code", "timeout", "signal", "core-dump",
                    "watchdog", "start-limit-hit", "protocol", "resources",
                    "dependency", "UNKNOWN"})
GATES = frozenset({"PASS", "FAIL", "NO_VERIFICADO"})
INVARIANTS = {
    "read_only": True, "host_metadata_only": True, "database_opened": False,
    "database_bytes_read": 0, "host_db_writes": False,
    "host_purge_allowed": False, "physical_host_delete_count": 0,
    "provider_calls": 0, "order_routes_called": False,
    "ppi_watch_touched": False, "paper_ready": False,
    "paper_zero_checked": False, "writer_coverage": "NAMED_UNITS_ONLY",
    "a330_capture_certified": False,
}


class MetadataReject(Exception):
    """Expected local rejection; no exception text is published."""


def _dir_fd(path: Path) -> int:
    """Anchor absolute components with Linux O_PATH: no content/atime read."""
    if not path.is_absolute() or ".." in path.parts or not hasattr(os, "O_PATH"):
        raise MetadataReject("UNSAFE_PATH")
    flags = os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        descriptor = os.open("/", flags)
    except OSError as exc:
        raise MetadataReject("UNAVAILABLE") from exc
    try:
        for part in path.parts[1:]:
            nxt = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = nxt
        return descriptor
    except (OSError, ValueError) as exc:
        os.close(descriptor)
        raise MetadataReject("UNSAFE_PATH") from exc


def _stat_regular(path: Path, *, maximum: int, minimum: int = 0) -> tuple[str, int | None, tuple | None]:
    """No final-file open(), and no symlink traversal of parent directories."""
    try:
        directory = _dir_fd(path.parent)
        try:
            info = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        finally:
            os.close(directory)
    except MetadataReject as exc:
        return str(exc), None, None
    except FileNotFoundError:
        return "MISSING", None, None
    except (OSError, ValueError):
        return "UNAVAILABLE", None, None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        return "UNSAFE_FILE", None, None
    if not minimum <= info.st_size <= maximum:
        return "SIZE_LIMIT", None, None
    identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                info.st_ctime_ns, info.st_nlink, info.st_mode)
    return "PRESENT_REGULAR", info.st_size, identity


def _scan() -> list[dict]:
    result = []
    for role, path, maximum in DATABASES:
        status, size, marker = _stat_regular(path, maximum=maximum, minimum=4096)
        sidecars = {}
        sidecar_markers = {}
        for name in SIDECARS:
            side_status, _, side_marker = _stat_regular(
                Path(str(path) + SUFFIX[name]), maximum=maximum)
            sidecars[name] = "PRESENT" if side_status == "PRESENT_REGULAR" else (
                side_status if side_status in SIDECAR_STATES else "UNSAFE_FILE")
            sidecar_markers[name] = side_marker
        result.append({"role": role, "status": status, "size_bytes": size,
                       "sidecars": sidecars, "_marker": marker,
                       "_sidecar_markers": sidecar_markers})
    return result


def _stability(before: list[dict], after: list[dict]) -> list[dict]:
    result = []
    for old, new in zip(before, after):
        record = {"role": old["role"], "status": old["status"],
                  "size_bytes": old["size_bytes"], "sidecars": dict(old["sidecars"])}
        if (old["status"], old["size_bytes"], old["_marker"]) != (
                new["status"], new["size_bytes"], new["_marker"]):
            record.update(status="CHANGED_DURING_PROBE", size_bytes=None)
        for name in SIDECARS:
            if (old["sidecars"][name], old["_sidecar_markers"][name]) != (
                    new["sidecars"][name], new["_sidecar_markers"][name]):
                record["sidecars"][name] = "CHANGED_DURING_PROBE"
        result.append(record)
    return result


def _available_memory() -> int | None:
    try:
        # /proc is kernel metadata, not a customer database or quote source.
        with open("/proc/meminfo", "r", encoding="ascii") as handle:
            raw = handle.read(4096)
        match = re.search(r"^MemAvailable:\s+(\d+) kB$", raw, re.M)
        if match is None:
            return None
        value = int(match.group(1)) * 1024
        return value if 0 <= value <= MAX_NUMBER else None
    except (OSError, UnicodeError, ValueError):
        return None


def _disk_space(path: Path) -> tuple[int | None, int | None]:
    try:
        descriptor = _dir_fd(path)
        try:
            info = os.fstatvfs(descriptor)
        finally:
            os.close(descriptor)
        free = info.f_bavail * info.f_frsize
        total = info.f_blocks * info.f_frsize
        return (free, total) if 0 <= free <= total <= MAX_NUMBER else (None, None)
    except (MetadataReject, OSError, ValueError):
        return None, None


def _gate(value: int | None, lower: int) -> str:
    return "NO_VERIFICADO" if value is None else ("PASS" if value >= lower else "FAIL")


def _unit(role: str, name: str, deadline: float) -> dict:
    record = {"role": role, "load": "UNKNOWN", "active": "UNKNOWN",
              "unit_file": "UNKNOWN", "result": "UNKNOWN"}
    remaining = deadline - time.monotonic()
    if remaining < .4:
        return record
    try:
        outcome = subprocess.run(
            ["systemctl", "show", name,
             "--property=LoadState,ActiveState,UnitFileState,Result"],
            capture_output=True, text=True, timeout=min(2.2, remaining), check=False)
        if outcome.returncode != 0 or len(outcome.stdout) > 2048:
            return record
        lines = outcome.stdout.splitlines()
        # Duplicate or malformed fields cannot be collapsed into a false
        # inactive/masked positive by Python dict's last-key-wins behavior.
        if len(lines) != 4 or any(line.count("=") != 1 for line in lines):
            return record
        pairs = [line.split("=", 1) for line in lines]
        if len({key for key, _ in pairs}) != len(pairs):
            return record
        values = dict(pairs)
        if len(values) != 4 or set(values) != {
                "LoadState", "ActiveState", "UnitFileState", "Result"}:
            return record
        for key, output, allowed in (("LoadState", "load", LOAD),
                                     ("ActiveState", "active", ACTIVE),
                                     ("UnitFileState", "unit_file", UNIT_FILE),
                                     ("Result", "result", RESULT)):
            record[output] = values[key] if values[key] in allowed else "UNKNOWN"
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return record


def _timer_gate(units: list[dict]) -> str:
    timers = units[:3]
    # systemd may represent a genuinely masked unit with LoadState=masked,
    # not loaded. The safe positive is inactive + UnitFileState=masked.
    if any(row["load"] not in {"loaded", "masked"} or row["active"] == "UNKNOWN"
           or row["unit_file"] == "UNKNOWN"
           for row in timers):
        return "NO_VERIFICADO"
    if any(row["load"] == "masked" and row["unit_file"] != "masked"
           for row in timers):
        return "NO_VERIFICADO"
    return "PASS" if all(row["active"] == "inactive" and
                         row["unit_file"] == "masked" for row in timers) else "FAIL"


def _eod_gate(units: list[dict]) -> str:
    # Both fixed services can write historical state. A PASS for only the
    # first service would falsely suggest writer quiescence while postclose
    # is active. This is still only NAMED_UNITS_ONLY, never a global lock GO.
    writers = units[3:5]
    if any(row["load"] == "loaded" and row["active"] in {
            "active", "activating", "deactivating", "reloading"} for row in writers):
        return "FAIL"
    if any(row["load"] != "loaded" or row["active"] in {"UNKNOWN", "failed"}
           for row in writers):
        return "NO_VERIFICADO"
    return "PASS" if all(row["active"] == "inactive" for row in writers) else "FAIL"


def _receipt() -> dict:
    started = time.monotonic()
    initial = _scan()
    mem = _available_memory()
    # A330 gates free space on '/'; DATA may be a different mount. Report
    # both rather than falsely equating the data filesystem with root.
    root_free, root_total = _disk_space(Path("/"))
    data_free, data_total = _disk_space(DATA)
    deadline = started + 23
    units = [_unit(role, name, deadline) for role, name in UNITS]
    final = _scan()
    return {"schema": SCHEMA, "state": "METADATA_ONLY", **INVARIANTS,
            "files": _stability(initial, final),
            "resources": {"mem_available_bytes": mem,
                          "disk_free_root_bytes": root_free,
                          "disk_total_root_bytes": root_total,
                          "disk_free_data_bytes": data_free,
                          "disk_total_data_bytes": data_total,
                          "memory_gate": _gate(mem, MIN_MEMORY),
                          "disk_gate": _gate(root_free, MIN_DISK)},
            "units": units, "a3_timer_gate": _timer_gate(units),
            "eod_writer_gate": _eod_gate(units)}


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


def _invalid_constant(_: str):
    raise ValueError("nonfinite")


def _integer(value: object, *, nullable: bool = False) -> bool:
    return (nullable and value is None) or (type(value) is int and 0 <= value <= MAX_NUMBER)


def _band(value: int | None, levels: tuple[tuple[int, str], ...], final: str) -> str:
    if value is None:
        return "UNKNOWN"
    for limit, label in levels:
        if value < limit:
            return label
    return final


def _size_band(value: int | None) -> str:
    return _band(value, ((256 * 1024**2, "BELOW_256M"),
                         (512 * 1024**2, "256M_TO_512M"),
                         (1024**3, "512M_TO_1G"),
                         (2 * 1024**3, "1G_TO_2G")), "2G_TO_4G")


def _memory_band(value: int | None) -> str:
    return _band(value, ((256 * 1024**2, "BELOW_256M"),
                         (512 * 1024**2, "256M_TO_512M"),
                         (1024**3, "512M_TO_1G")), "ABOVE_1G")


def _disk_band(value: int | None) -> str:
    return _band(value, ((2 * 1024**3, "BELOW_2G"),
                         (4 * 1024**3, "2G_TO_4G"),
                         (8 * 1024**3, "4G_TO_8G")), "ABOVE_8G")


def _validate(raw: bytes) -> dict:
    if len(raw) > MAX_RAW:
        raise MetadataReject("OVERSIZE")
    data = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_no_duplicates,
                      parse_constant=_invalid_constant)
    expected = {"schema", "state", "files", "resources", "units",
                "a3_timer_gate", "eod_writer_gate", *INVARIANTS}
    if (type(data) is not dict or set(data) != expected or data["schema"] != SCHEMA
            or data["state"] != "METADATA_ONLY" or any(
                type(data[k]) is not type(v) or data[k] != v
                for k, v in INVARIANTS.items())):
        raise MetadataReject("SCHEMA")
    files = data["files"]
    if type(files) is not list or len(files) != len(DATABASES):
        raise MetadataReject("FILE_COUNT")
    for row, (role, _, maximum) in zip(files, DATABASES):
        if (type(row) is not dict or set(row) != {"role", "status", "size_bytes", "sidecars"}
                or row["role"] != role or row["status"] not in FILE_STATES
                or type(row["sidecars"]) is not dict or set(row["sidecars"]) != set(SIDECARS)
                or any(type(v) is not str or v not in SIDECAR_STATES
                       for v in row["sidecars"].values())):
            raise MetadataReject("FILE")
        size = row["size_bytes"]
        if row["status"] == "PRESENT_REGULAR":
            if not _integer(size) or not 4096 <= size <= maximum:
                raise MetadataReject("FILE_SIZE")
        elif size is not None:
            raise MetadataReject("FILE_STATUS_SIZE")
    resources = data["resources"]
    if (type(resources) is not dict or set(resources) != {
            "mem_available_bytes", "disk_free_root_bytes", "disk_total_root_bytes",
            "disk_free_data_bytes", "disk_total_data_bytes", "memory_gate", "disk_gate"}):
        raise MetadataReject("RESOURCES")
    mem = resources["mem_available_bytes"]
    root_free = resources["disk_free_root_bytes"]
    root_total = resources["disk_total_root_bytes"]
    data_free = resources["disk_free_data_bytes"]
    data_total = resources["disk_total_data_bytes"]
    if (not _integer(mem, nullable=True) or
            any(not _integer(v, nullable=True) for v in
                (root_free, root_total, data_free, data_total))
            or (root_free is None) != (root_total is None)
            or (data_free is None) != (data_total is None)
            or (root_free is not None and root_free > root_total)
            or (data_free is not None and data_free > data_total)
            or resources["memory_gate"] != _gate(mem, MIN_MEMORY)
            or resources["disk_gate"] != _gate(root_free, MIN_DISK)):
        raise MetadataReject("RESOURCE_GATES")
    units = data["units"]
    if type(units) is not list or len(units) != len(UNITS):
        raise MetadataReject("UNIT_COUNT")
    for row, (role, _) in zip(units, UNITS):
        if (type(row) is not dict or set(row) != {"role", "load", "active", "unit_file", "result"}
                or row["role"] != role or row["load"] not in LOAD
                or row["active"] not in ACTIVE or row["unit_file"] not in UNIT_FILE
                or row["result"] not in RESULT):
            raise MetadataReject("UNIT")
    if (data["a3_timer_gate"] not in GATES or data["a3_timer_gate"] != _timer_gate(units)
            or data["eod_writer_gate"] not in GATES
            or data["eod_writer_gate"] != _eod_gate(units)):
        raise MetadataReject("UNIT_GATES")
    # The only public result is reconstructed from fixed enums. Exact bytes
    # remain in the private runner payload and never reach the artifact.
    return {**data,
            "files": [{"role": row["role"], "status": row["status"],
                       "size_band": _size_band(row["size_bytes"]),
                       "sidecars": dict(row["sidecars"])} for row in files],
            "resources": {"memory_band": _memory_band(mem),
                          "root_disk_band": _disk_band(root_free),
                          "data_disk_band": _disk_band(data_free),
                          "memory_gate": resources["memory_gate"],
                          "disk_gate": resources["disk_gate"]}}


def sanitize(raw: bytes, remote_rc: int) -> dict:
    """Never echo untrusted raw JSON, stderr, paths, IDs, or exception text."""
    failure = ("EXIT_124_TIMEOUT_OR_REMOTE" if remote_rc == 124 else
               "SSH_255_TRANSPORT_OR_REMOTE" if remote_rc == 255 else
               "REMOTE_EXIT_NONZERO" if remote_rc != 0 else "RECEIPT_INVALID")
    try:
        if remote_rc == 0:
            return _validate(raw)
    except (MetadataReject, UnicodeError, ValueError, TypeError, KeyError):
        pass
    return {"schema": SCHEMA, "state": "NO_VERIFICADO", **INVARIANTS,
            "failure_class": failure, "files": [], "resources": {}, "units": [],
            "a3_timer_gate": "NO_VERIFICADO", "eod_writer_gate": "NO_VERIFICADO"}


def _main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("mode", choices=("host-probe", "sanitize"))
    parser.add_argument("--raw")
    parser.add_argument("--out")
    parser.add_argument("--remote-rc", type=int)
    args = parser.parse_args()
    if args.mode == "host-probe":
        if args.raw is not None or args.out is not None or args.remote_rc is not None:
            return 2
        try:
            result = _receipt()
        except (OSError, ValueError, MetadataReject, subprocess.TimeoutExpired):
            return 2
        sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
        return 0
    if args.raw is None or args.out is None or args.remote_rc is None:
        return 2
    # Runner-only files are fixed and private by workflow convention.
    raw_path = Path(args.raw)
    output_path = Path(args.out)
    if (raw_path.name != ".a491-raw.json" or output_path.name != "a491-sanitized.json"
            or raw_path.parent != output_path.parent or not output_path.parent.is_dir()
            or output_path.parent.is_symlink()):
        return 2
    try:
        fd = os.open(raw_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            raw = handle.read(MAX_RAW + 1) if stat.S_ISREG(info.st_mode) and info.st_nlink == 1 else b""
    except OSError:
        raw = b""
    result = sanitize(raw, args.remote_rc)
    output_path.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n",
                           encoding="utf-8")
    return 0 if result["state"] == "METADATA_ONLY" else 2


if __name__ == "__main__":
    raise SystemExit(_main())
