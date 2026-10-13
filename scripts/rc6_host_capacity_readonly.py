#!/usr/bin/env python3
"""Bounded host observations, streamed through Actions; never a deploy admission.

No Docker exec, process command lines, environments, profiles, database scans,
cleanup, or host output files.  SQLite reads one indexed safety row in a child
that has first denied filesystem writes using Landlock, including WAL/SHM.
Every exported string has a closed grammar; subprocess stderr stays private.
"""
from __future__ import annotations

import argparse
import ast
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import select
import signal
import sqlite3
import stat
import subprocess
import sys
import time


SCHEMA = "porota.rc6.host-capacity-readonly.v1"
ROOT = Path("/opt/porota-trading")
DB = ROOT / "data/paper_v17/observer_v17.db"
CONTAINERS = ("porota_production_observer", "porota_production_dashboard")
UNITS = (
    "docker.service", "containerd.service", "systemd-journald.service",
    "porota-preopen-rc6.service", "porota-preopen-rc6.timer",
    "porota-runtime-evidence-rc6.service", "porota-runtime-evidence-rc6.timer",
    "porota-history-postclose-rc6.service", "porota-history-postclose-rc6.timer",
    "porota-snapshot-preopen-rc6.timer", "porota-snapshot-postclose-rc6.timer",
    "porota-full-db-integrity-rc6.service", "porota-full-db-integrity-rc6.timer",
    "porota-candle-integrity-rc6.service", "porota-candle-integrity-rc6.timer",
    "porota-fast-functional-health-rc6.service", "porota-fast-functional-health-rc6.timer",
    "porota-host-general-backup-rc6.service", "porota-host-general-backup-rc6.timer",
    "porota-iol-shadow-collector-rc6.service", "porota-gdelt-event-risk-rc6.service",
)
SERVICE_PROPERTIES = (
    "Id", "LoadState", "ActiveState", "SubState", "UnitFileState", "Result",
    "MainPID", "CPUUsageNSec", "MemoryCurrent", "MemoryPeak", "TasksCurrent",
    "ExecMainStatus", "ExecMainStartTimestampMonotonic", "LastTriggerUSecMonotonic",
    "NextElapseUSecMonotonic",
)
MAX_RECEIPT_BYTES = 256 * 1024
MAX_PROCESSES = 2048
SAMPLE_COUNT = 3
SAMPLE_INTERVAL_SECONDS = 2
MAX_HEARTBEAT_AGE_SECONDS = 120
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA64 = re.compile(r"[0-9a-f]{64}\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
SAFE_ENUM = re.compile(r"[A-Za-z0-9_.-]{1,96}\Z")
SESSION = re.compile(r"[A-Za-z0-9_.:-]{1,96}\Z")
ISO = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?\+00:00\Z")
QUERY = "SELECT mode,real_orders_sent,heartbeat_at FROM observer_state WHERE id=1"
REMOTE_ERROR_CODES = frozenset({
    "OBSERVATION_OSERROR", "OBSERVATION_NOT_REGULAR_FILE", "OBSERVATION_INPUT_TOO_LARGE",
    "AUDIT_STRUCTURAL_FORMAT_INVALID", "AUDIT_SOURCE_IDENTITY_INVALID", "AUDIT_CONTROLLER_IDENTITY_INVALID",
    "AUDIT_ACTIONS_IDENTITY_INVALID", "AUDIT_PUBLIC_SCHEMA_OR_SOURCE_INVALID", "AUDIT_PUBLIC_NESTED_SCHEMA_INVALID",
    "AUDIT_PUBLIC_DEPTH_BOUND", "AUDIT_PUBLIC_KEY_INVALID", "AUDIT_PUBLIC_LIST_BOUND", "AUDIT_PUBLIC_STRING_INVALID",
    "AUDIT_PUBLIC_SENSITIVE_STRING", "AUDIT_PUBLIC_NUMBER_INVALID", "AUDIT_PUBLIC_VALUE_INVALID",
    "AUDIT_BLOCKERS_NOT_RECOMPUTED", "AUDIT_SAMPLE_ARITHMETIC_INVALID", "AUDIT_RUNTIME_BRACKETS_INCOMPLETE",
    "AUDIT_RUNTIME_ROLE_INVALID", "AUDIT_METADATA_ROLE_INVALID", "AUDIT_SERVICES_ALLOWLIST_INVALID",
    "AUDIT_CPU_LOAD_SCHEMA_INVALID", "AUDIT_RESERVES_OR_PROFILE_AUTHORITY_CHANGED", "AUDIT_INVARIANTS_CHANGED",
    "AUDIT_CANNOT_GRANT_RELEASE_ADMISSION",
})
DOCKER_FORMAT = (
    '{"id":{{json .Id}},"image_id":{{json .Image}},'
    '"running":{{json .State.Running}},"pid":{{json .State.Pid}},'
    '"started_at":{{json .State.StartedAt}},"restart_count":{{json .RestartCount}},'
    '"memory_limit_bytes":{{json .HostConfig.Memory}},'
    '"nano_cpus":{{json .HostConfig.NanoCpus}},'
    '"pids_limit":{{json .HostConfig.PidsLimit}},'
    '"readonly_rootfs":{{json .HostConfig.ReadonlyRootfs}}}'
)


class ObservationError(RuntimeError):
    """Only stable uppercase error codes can escape the collector."""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def number(value, *, maximum=2**63 - 1):
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if 0 <= parsed <= maximum else None


def enum(value):
    return value if isinstance(value, str) and SAFE_ENUM.fullmatch(value) else "NO_VERIFICADO"


def sha(value, *, digest=False):
    pattern = DIGEST if digest else SHA40
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def bounded_read(path: Path, limit=65536) -> str:
    """Never follow the final path component or accept devices/FIFOs."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ObservationError("OBSERVATION_NOT_REGULAR_FILE")
        raw = os.read(fd, limit + 1)
        if len(raw) > limit:
            raise ObservationError("OBSERVATION_INPUT_TOO_LARGE")
        return raw.decode("utf-8")
    finally:
        os.close(fd)


def read_json(path: Path):
    try:
        value = json.loads(bounded_read(path))
        return value if isinstance(value, dict) else {}
    except (OSError, ObservationError, UnicodeError, json.JSONDecodeError):
        return {}


def run(argv: list[str], *, timeout=5):
    """Closed command families; no shells, free arguments, or stderr export."""
    allowed = (
        argv == ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        len(argv) == 5 and argv[:3] == ["docker", "inspect", "--format"]
        and argv[3] == DOCKER_FORMAT and argv[4] in CONTAINERS,
        argv == ["docker", "image", "ls", "--no-trunc", "--format", "{{.ID}}"],
        argv == ["docker", "info", "--format", "{{.DockerRootDir}}"],
        len(argv) == 5 and argv[:3] == ["docker", "image", "inspect"]
        and DIGEST.fullmatch(argv[3] or "")
        and argv[4] == "--format={{.Size}}",
        argv == ["systemctl", "show", "--no-pager", "--property=" + ",".join(SERVICE_PROPERTIES), *UNITS],
    )
    if not any(allowed):
        raise ObservationError("COMMAND_NOT_ALLOWLISTED")
    try:
        p = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=timeout, check=False,
                           env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"})
    except subprocess.TimeoutExpired:
        raise ObservationError("READONLY_COMMAND_TIMEOUT") from None
    except OSError:
        raise ObservationError("READONLY_COMMAND_UNAVAILABLE") from None
    if p.returncode:
        raise ObservationError("READONLY_COMMAND_FAILED")
    if len(p.stdout) > MAX_RECEIPT_BYTES:
        raise ObservationError("READONLY_COMMAND_OUTPUT_TOO_LARGE")
    return p.stdout.decode("utf-8")


def docker_filesystem():
    try:
        value = run(["docker", "info", "--format", "{{.DockerRootDir}}"] ).strip()
        if not re.fullmatch(r"/(?:var/lib|opt|srv)/[A-Za-z0-9_./-]{1,160}", value) or ".." in Path(value).parts:
            raise ObservationError("DOCKER_DATA_ROOT_NOT_ALLOWLISTED")
        return filesystem("DOCKER", Path(value))
    except ObservationError as exc:
        return {"role": "DOCKER", "state": "NO_VERIFICADO", "error_code": str(exc)}


def filesystem(role: str, path: Path):
    try:
        st, fs = path.stat(), os.statvfs(path)
        best = None
        for line in bounded_read(Path("/proc/self/mountinfo")).splitlines():
            before, after = line.split(" - ", 1)
            fields = before.split()
            mount = fields[4].replace("\\040", " ")
            if str(path) == mount or str(path).startswith(mount.rstrip("/") + "/"):
                if best is None or len(mount) > best[0]:
                    best = (len(mount), int(fields[0]), after.split()[0])
        return {"role": role, "state": "OBSERVED", "device_major": os.major(st.st_dev),
                "device_minor": os.minor(st.st_dev), "filesystem_type": enum(best[2]) if best else "NO_VERIFICADO",
                "mount_id": best[1] if best else None, "allocation_unit_bytes": fs.f_frsize,
                "total_bytes": fs.f_blocks * fs.f_frsize,
                "available_bytes": fs.f_bavail * fs.f_frsize,
                "free_bytes": fs.f_bfree * fs.f_frsize,
                "inode_total": fs.f_files, "inode_available": fs.f_favail,
                "inode_free_percent": round(100 * fs.f_favail / fs.f_files, 3) if fs.f_files else None,
                "readonly": bool(fs.f_flag & os.ST_RDONLY)}
    except (OSError, ObservationError, ValueError, IndexError):
        return {"role": role, "state": "NO_VERIFICADO", "error_code": "FILESYSTEM_OBSERVATION_UNAVAILABLE"}


def file_metadata(role: str, path: Path):
    try:
        st = path.lstat()
        if not stat.S_ISREG(st.st_mode):
            raise ObservationError("DATABASE_METADATA_NOT_REGULAR")
        return {"role": role, "state": "OBSERVED", "logical_bytes": st.st_size,
                "allocated_bytes": st.st_blocks * 512, "inode": st.st_ino,
                "device_major": os.major(st.st_dev), "device_minor": os.minor(st.st_dev),
                "mtime_ns": st.st_mtime_ns, "uid": st.st_uid, "gid": st.st_gid}
    except FileNotFoundError:
        return {"role": role, "state": "ABSENT"}
    except (OSError, ObservationError):
        return {"role": role, "state": "NO_VERIFICADO", "error_code": "DATABASE_METADATA_UNAVAILABLE"}


def key_values(text: str):
    return {line.split()[0].rstrip(":"): number(line.split()[1])
            for line in text.splitlines() if len(line.split()) >= 2}


def process(pid: int):
    try:
        fields = bounded_read(Path(f"/proc/{pid}/stat"), 8192).rsplit(")", 1)[1].split()
        return {"pid": pid, "start_ticks": number(fields[19]),
                "cpu_ticks": int(fields[11]) + int(fields[12]),
                "rss_bytes": int(fields[21]) * os.sysconf("SC_PAGE_SIZE")}
    except (OSError, ObservationError, ValueError, IndexError):
        return {"pid": pid, "error_code": "PROCESS_IDENTITY_UNAVAILABLE"}


def cgroup(pid: int):
    try:
        lines = bounded_read(Path(f"/proc/{pid}/cgroup"), 8192).splitlines()
        unified = [line[3:] for line in lines if line.startswith("0::/")]
        if len(unified) != 1 or ".." in Path(unified[0]).parts:
            raise ObservationError("CGROUP_V2_UNAVAILABLE")
        base = Path("/sys/fs/cgroup") / unified[0].lstrip("/")
        if base.resolve() != base:
            raise ObservationError("CGROUP_PATH_ALIAS")
        out = {"state": "OBSERVED", "path_sha256": hashlib.sha256(unified[0].encode()).hexdigest()}
        for filename in ("memory.current", "memory.peak", "memory.max", "memory.swap.current", "memory.swap.max",
                         "pids.current", "pids.max"):
            try:
                value = bounded_read(base / filename, 4096).strip()
                out[filename.replace(".", "_")] = "UNLIMITED" if value == "max" else number(value)
            except OSError:
                out[filename.replace(".", "_")] = None
        quota, period = bounded_read(base / "cpu.max", 4096).split()
        out["cpu_quota_usec"] = "UNLIMITED" if quota == "max" else number(quota)
        out["cpu_period_usec"] = number(period)
        for filename, keys in (("cpu.stat", ("usage_usec", "nr_throttled", "throttled_usec")),
                               ("memory.stat", ("anon", "file", "shmem", "kernel", "pagetables", "slab")),
                               ("memory.events", ("oom", "oom_kill", "high", "max"))):
            values = key_values(bounded_read(base / filename, 16384))
            out[filename.replace(".", "_")] = {key: values.get(key) for key in keys}
        return out
    except (OSError, ObservationError, ValueError):
        return {"state": "NO_VERIFICADO", "error_code": "CGROUP_OBSERVATION_UNAVAILABLE"}


def sample():
    memory = key_values(bounded_read(Path("/proc/meminfo"), 16384))
    keys = ("MemTotal", "MemAvailable", "MemFree", "Buffers", "Cached", "SReclaimable", "Shmem",
            "Dirty", "Writeback", "SwapTotal", "SwapFree")
    cpu = next(line for line in bounded_read(Path("/proc/stat")).splitlines() if line.startswith("cpu "))
    ticks = [int(value) for value in cpu.split()[1:9]]  # guest time is already included in user/nice
    load = [float(value) for value in bounded_read(Path("/proc/loadavg"), 4096).split()[:3]]
    pids = [int(path.name) for path in Path("/proc").iterdir() if path.name.isdecimal()]
    if len(pids) > MAX_PROCESSES:
        raise ObservationError("PROCESS_METADATA_BOUND_EXCEEDED")
    statuses = [process(pid) for pid in pids]
    return {"observed_at": utcnow(), "monotonic_ns": time.monotonic_ns(),
            "online_cpu_count": os.sysconf("SC_NPROCESSORS_ONLN"),
            "clock_ticks_per_second": os.sysconf("SC_CLK_TCK"),
            "cpu_total_ticks": sum(ticks), "cpu_idle_iowait_ticks": sum(ticks[3:5]),
            "load_average": load,
            "memory_bytes": {key: memory[key] * 1024 if memory.get(key) is not None else None for key in keys},
            "process_count": len(pids),
            "process_rss_sum_bytes": sum(item.get("rss_bytes", 0) for item in statuses),
            "processes_disappeared_during_sample": sum("error_code" in item for item in statuses),
            "rss_sum_metric": "SUM_RSS_INCLUDES_SHARED_PAGES_NOT_UNIQUE_HOST_MEMORY"}


def cpu_intervals(samples):
    intervals = []
    for before, after in zip(samples, samples[1:]):
        total = after["cpu_total_ticks"] - before["cpu_total_ticks"]
        idle = after["cpu_idle_iowait_ticks"] - before["cpu_idle_iowait_ticks"]
        intervals.append({"elapsed_ns": after["monotonic_ns"] - before["monotonic_ns"],
                          "busy_percent": round(100 * (total - idle) / total, 3) if total > 0 and 0 <= idle <= total else None})
    return intervals


def containers():
    out = []
    for name in CONTAINERS:
        try:
            row = json.loads(run(["docker", "inspect", "--format", DOCKER_FORMAT, name]))
            pid = number(row.get("pid"))
            if not pid:
                raise ObservationError("CONTAINER_IDENTITY_INVALID")
            identity = row.get("id")
            if not isinstance(identity, str) or not SHA64.fullmatch(identity):
                raise ObservationError("CONTAINER_IDENTITY_INVALID")
            item = {"role": name, "state": "OBSERVED", "container_id": identity,
                    "image_id": sha(row.get("image_id"), digest=True), "running": row.get("running") is True,
                    "started_at": timestamp(row.get("started_at")), "restart_count": number(row.get("restart_count")),
                    "memory_limit_bytes": number(row.get("memory_limit_bytes")), "nano_cpus": number(row.get("nano_cpus")),
                    "pids_limit": number(row.get("pids_limit")), "readonly_rootfs": row.get("readonly_rootfs") is True,
                    "process": process(pid), "cgroup": cgroup(pid)}
            if not item["image_id"]:
                raise ObservationError("CONTAINER_IMAGE_IDENTITY_INVALID")
            try:
                item["reported_logical_image_bytes"] = number(run(["docker", "image", "inspect", item["image_id"], "--format={{.Size}}"] ).strip())
            except ObservationError:
                item["reported_logical_image_bytes"] = None
            out.append(item)
        except (ObservationError, json.JSONDecodeError, TypeError) as exc:
            code = str(exc) if isinstance(exc, ObservationError) else "CONTAINER_OBSERVATION_INVALID"
            out.append({"role": name, "state": "NO_VERIFICADO", "error_code": code})
    return out


def image_inventory():
    try:
        ids = run(["docker", "image", "ls", "--no-trunc", "--format", "{{.ID}}"] ).splitlines()
        if len(ids) > 256 or any(not DIGEST.fullmatch(item) for item in ids):
            raise ObservationError("IMAGE_INVENTORY_BOUND_OR_IDENTITY_INVALID")
        return {"state": "OBSERVED", "unique_image_count": len(set(ids)),
                "identity_set_sha256": hashlib.sha256("\n".join(sorted(set(ids))).encode()).hexdigest(),
                "physical_unique_layer_bytes": None,
                "physical_layer_load_state": "NO_VERIFICADO_NO_HEAVY_SCAN"}
    except ObservationError as exc:
        return {"state": "NO_VERIFICADO", "error_code": str(exc)}


def services():
    try:
        text = run(["systemctl", "show", "--no-pager", "--property=" + ",".join(SERVICE_PROPERTIES), *UNITS], timeout=10)
        out = []
        for section in text.strip().split("\n\n"):
            values = dict(line.split("=", 1) for line in section.splitlines() if "=" in line)
            if values.get("Id") not in UNITS:
                raise ObservationError("SERVICE_IDENTITY_NOT_ALLOWLISTED")
            numeric = set(SERVICE_PROPERTIES[6:])
            out.append({key: number(values.get(key)) if key in numeric else enum(values.get(key)) for key in SERVICE_PROPERTIES})
        if len(out) != len(UNITS) or len({item["Id"] for item in out}) != len(UNITS):
            raise ObservationError("SERVICE_INVENTORY_INCOMPLETE")
        return {"state": "OBSERVED", "units": out}
    except (ObservationError, ValueError) as exc:
        return {"state": "NO_VERIFICADO", "error_code": str(exc) if isinstance(exc, ObservationError) else "SERVICE_OBSERVATION_INVALID"}


def deny_filesystem_writes():
    """Deny open-for-write/create/unlink/truncate in the safety child.

    No rules grant writes.  Existing non-stdio descriptors are closed first;
    the output pipe is the only surviving write FD.  SQLite may use existing
    read-only WAL/SHM, but cannot create or open them writable.  No immutable
    mode that ignores WAL is accepted.
    """
    if os.uname().machine not in {"x86_64", "aarch64"}:
        raise ObservationError("SAFETY_LANDLOCK_ARCH_UNSUPPORTED")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    abi = libc.syscall(444, ctypes.c_void_p(), 0, 1)
    if abi < 1:
        raise ObservationError("SAFETY_LANDLOCK_UNAVAILABLE")
    writes = sum(1 << bit for bit in (1, 4, 5, 6, 7, 8, 9, 10, 11, 12))
    if abi >= 2:
        writes |= 1 << 13  # REFER
    if abi >= 3:
        writes |= 1 << 14  # TRUNCATE
    attr = ctypes.c_uint64(writes)
    fd = libc.syscall(444, ctypes.byref(attr), ctypes.sizeof(attr), 0)
    if fd < 0:
        raise ObservationError("SAFETY_LANDLOCK_RULESET_FAILED")
    try:
        if libc.prctl(38, 1, 0, 0, 0) or libc.syscall(446, fd, 0):
            raise ObservationError("SAFETY_LANDLOCK_SEAL_FAILED")
    finally:
        os.close(fd)
    return {"abi": abi, "filesystem_writes_denied": True, "wal_ignored": False}


def safety_row(path: Path, *, seal=deny_filesystem_writes, connect=sqlite3.connect):
    protection = seal()  # Must precede connect, pragma and every database read.
    if not isinstance(protection, dict) or protection.get("filesystem_writes_denied") is not True or protection.get("wal_ignored") is not False:
        raise ObservationError("SAFETY_WRITE_SEAL_NOT_VERIFIED")
    if not stat.S_ISREG(path.lstat().st_mode) or any(parent.is_symlink() for parent in path.parents):
        raise ObservationError("SAFETY_DATABASE_PATH_ALIAS_OR_NOT_REGULAR")
    conn = connect(f"file:{path}?mode=ro", uri=True, timeout=0.2)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=200")
        if conn.execute("PRAGMA query_only").fetchone() != (1,):
            raise ObservationError("SAFETY_QUERY_ONLY_NOT_ENFORCED")
        vm_exhausted = False
        def stop_at_original_budget():
            nonlocal vm_exhausted
            vm_exhausted = True
            return 1
        conn.set_progress_handler(stop_at_original_budget, 1000)
        try:
            row = conn.execute(QUERY).fetchone()
        except sqlite3.Error as error:
            if vm_exhausted and getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_INTERRUPT:
                raise ObservationError("SAFETY_SQLITE_ORIGINAL_VM_BUDGET_EXCEEDED") from error
            raise
        if row is None or len(row) != 3:
            raise ObservationError("SAFETY_ROW_ABSENT")
        mode, orders, heartbeat = row
        return {"state": "OBSERVED", "mode": "PRODUCTION_PAPER" if mode == "PRODUCTION_PAPER" else "UNRECOGNIZED",
                "real_orders_sent": number(orders), "heartbeat_at": timestamp(heartbeat),
                "open_mode": "MODE_RO_QUERY_ONLY", "write_seal": protection}
    finally:
        conn.close()


def safety_child():
    """Bounded owned child, no stdout other than typed safety fields."""
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            for item in Path("/proc/self/fd").iterdir():
                fd = int(item.name)
                if fd != write_fd:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            try:
                result = safety_row(DB)
            except ObservationError as exc:
                result = {"state": "NO_VERIFICADO", "error_code": str(exc)}
            except sqlite3.Error as exc:
                result = {"state": "NO_VERIFICADO", "error_code": "SAFETY_SQLITE_READONLY_FAILED",
                          "sqlite_error_code": number(getattr(exc, "sqlite_errorcode", None))}
            except Exception:
                result = {"state": "NO_VERIFICADO", "error_code": "SAFETY_CHILD_FAILED"}
            os.write(write_fd, json.dumps(result, sort_keys=True).encode())
        finally:
            os._exit(0)
    os.close(write_fd)
    try:
        ready, _, _ = select.select([read_fd], [], [], 4)
        if not ready:
            os.kill(pid, signal.SIGKILL)  # Exact owned child only, never a runtime PID.
            return {"state": "NO_VERIFICADO", "error_code": "SAFETY_CHILD_TIMEOUT"}
        raw = os.read(read_fd, 8193)
        if len(raw) > 8192:
            return {"state": "NO_VERIFICADO", "error_code": "SAFETY_CHILD_OUTPUT_BOUND"}
        return json.loads(raw)
    except (OSError, ValueError):
        return {"state": "NO_VERIFICADO", "error_code": "SAFETY_CHILD_OUTPUT_INVALID"}
    finally:
        os.close(read_fd)
        os.waitpid(pid, 0)


def runtime_identity():
    try:
        head = sha(run(["git", "-C", str(ROOT), "rev-parse", "HEAD"]).strip())
    except ObservationError:
        head = None
    version = {}
    try:
        for node in ast.parse(bounded_read(ROOT / "_version.py", 8192)).body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in {"MODE", "REAL_ORDER_CAPABILITY", "VERSION"}:
                        version[target.id] = enum(node.value.value)
    except (OSError, SyntaxError, ObservationError, UnicodeError):
        pass
    mode = read_json(ROOT / "data/operation_mode.json")
    deploy = read_json(ROOT / "data/deploy/CURRENT_STATE_V2.json")
    apis = mode.get("apis") if isinstance(mode.get("apis"), dict) else {}
    return {"host_repository_sha": head, "repository_version": version,
            "operation_mode": enum(mode.get("mode")), "execution": enum(mode.get("execution")),
            "real_order_route_capability": "BLOCKED" if str(apis.get("PPI_ORDERS") or deploy.get("real_order_capability")).upper() == "BLOCKED" else "NO_VERIFICADO",
            "recorded_real_order_routes": "NOT_CALLED" if deploy.get("real_order_routes") == "NOT_CALLED" else "NO_VERIFICADO",
            "recorded_candidate_sha": sha(deploy.get("candidate_sha")),
            "recorded_deploy_sha": sha(deploy.get("deploy_sha")),
            "recorded_image_id": sha(deploy.get("image_id"), digest=True),
            "deploy_state_recorded_at": timestamp(deploy.get("recorded_at"))}


def blockers(receipt):
    reasons = []
    identities = receipt["runtime_identities"]
    if identities[0] != identities[-1]:
        reasons.append("RUNTIME_IDENTITY_CHANGED_DURING_AUDIT")
    live = receipt["runtime_containers"]
    for group in live:
        if len(group) != len(CONTAINERS) or any(item.get("state") != "OBSERVED" or not item.get("running") for item in group):
            reasons.append("LIVE_RUNTIME_CONTAINER_UNVERIFIED")
    if all(all(item.get("state") == "OBSERVED" for item in group) for group in live):
        for before, after in zip(live[0], live[-1]):
            if (any(before.get(key) != after.get(key) for key in ("container_id", "image_id", "started_at", "restart_count"))
                    or any(before.get("process", {}).get(key) != after.get("process", {}).get(key) for key in ("pid", "start_ticks"))
                    or before.get("cgroup", {}).get("path_sha256") != after.get("cgroup", {}).get("path_sha256")):
                reasons.append("LIVE_RUNTIME_IDENTITY_CHANGED_DURING_AUDIT")
            if not after.get("process", {}).get("start_ticks") or after.get("cgroup", {}).get("state") != "OBSERVED":
                reasons.append("LIVE_RUNTIME_PROCESS_CGROUP_UNVERIFIED")
            if after.get("image_id") != identities[-1].get("recorded_image_id"):
                reasons.append("LIVE_IMAGE_VS_RECORDED_IMAGE_UNVERIFIED")
    for row in receipt["safety_samples"]:
        if row.get("state") != "OBSERVED":
            reasons.append(row.get("error_code", "SAFETY_OBSERVATION_UNAVAILABLE"))
            continue
        if row.get("write_seal", {}).get("filesystem_writes_denied") is not True or row.get("write_seal", {}).get("wal_ignored") is not False:
            reasons.append("SAFETY_WRITE_SEAL_NOT_VERIFIED")
        if row.get("mode") != "PRODUCTION_PAPER" or row.get("real_orders_sent") != 0:
            reasons.append("LIVE_PAPER_SAFETY_REJECTED")
        stamp = row.get("heartbeat_at")
        age = (datetime.fromisoformat(receipt["completed_at"]) - datetime.fromisoformat(stamp)).total_seconds() if stamp else None
        if age is None or not 0 <= age <= MAX_HEARTBEAT_AGE_SECONDS:
            reasons.append("LIVE_RUNTIME_HEARTBEAT_NOT_FRESH")
    ident = identities[-1]
    if ident["operation_mode"] != "PRODUCTION_PAPER" or ident["execution"] != "SIMULATED" or ident["real_order_route_capability"] != "BLOCKED":
        reasons.append("RECORDED_PAPER_CONFIGURATION_UNVERIFIED")
    if len(receipt["host_samples"]) != SAMPLE_COUNT or receipt["host_sample_errors"]:
        reasons.append("HOST_RESOURCE_OBSERVATION_UNAVAILABLE")
    if receipt["services_and_timers"].get("state") != "OBSERVED":
        reasons.append("SERVICES_TIMERS_OBSERVATION_UNAVAILABLE")
    if any(item.get("state") != "OBSERVED" for item in receipt["filesystems"]):
        reasons.append("FILESYSTEM_RESOURCE_OBSERVATION_UNAVAILABLE")
    # Observations cannot certify the new artifact or its workload/host margins.
    reasons.extend(("EXACT_CANDIDATE_PROFILE_AND_CONCURRENT_PEAK_REQUIRED",
                    "EXACT_ARTIFACT_TRANSFER_AND_LAYER_LOAD_REQUIRED",
                    "OS_AND_SERVICES_MEMORY_RESERVE_NOT_CERTIFIED"))
    return sorted(set(reasons))


def collect(identity):
    start = utcnow()
    samples = []
    errors = []
    first_identity, first_containers, first_safety = runtime_identity(), containers(), safety_child()
    for index in range(SAMPLE_COUNT):
        try:
            samples.append(sample())
        except (OSError, ObservationError, ValueError, StopIteration) as exc:
            errors.append(str(exc) if isinstance(exc, ObservationError) else "HOST_SAMPLE_UNAVAILABLE")
        if index + 1 < SAMPLE_COUNT:
            time.sleep(SAMPLE_INTERVAL_SECONDS)
    receipt = {"schema": SCHEMA, "scope": "READ_ONLY_EXISTING_HOST_METADATA",
               "source": identity, "started_at": start, "completed_at": utcnow(),
               "host_boot_identity_sha256": hashlib.sha256(bounded_read(Path("/proc/sys/kernel/random/boot_id"), 128).strip().encode()).hexdigest(),
               "host_samples": samples, "host_sample_errors": errors, "cpu_intervals": cpu_intervals(samples),
               "runtime_identities": [first_identity, runtime_identity()],
               "runtime_containers": [first_containers, containers()],
               "safety_samples": [first_safety, safety_child()],
               "filesystems": [filesystem("ROOT", Path("/")), filesystem("DATA", ROOT / "data"), docker_filesystem()],
               "database_metadata": [file_metadata("MAIN", DB), file_metadata("WAL", Path(str(DB) + "-wal")), file_metadata("SHM", Path(str(DB) + "-shm"))],
               "services_and_timers": services(), "docker_images": image_inventory(),
               "resource_reserves": {"disk_residual_bytes": 2147483648, "minimum_free_inode_percent": 10,
                                     "memory_os_services_reserve_bytes": None, "candidate_memory_peak_bytes": None,
                                     "candidate_profile_state": "NO_VERIFICADO", "artifact_transfer_state": "NO_VERIFICADO"},
               "invariants": {"host_mutations": "NONE", "docker_exec": "NOT_CALLED", "order_routes": "NOT_CALLED",
                              "profiles_secrets_environments": "NOT_READ", "ppi_watch": "UNTOUCHED",
                              "recurrent_additional_usd": 0, "deploy_authorized": False},
               "audit_state": "OBSERVED_READ_ONLY", "host_release_admission": "NO_VERIFICADO",
               "host_margin_green": False}
    receipt["completed_at"] = utcnow()
    receipt["blockers"] = blockers(receipt)
    return receipt


def identity(args):
    if not SHA40.fullmatch(args.source_sha or "") or not SHA40.fullmatch(args.source_tree or ""):
        raise ObservationError("AUDIT_SOURCE_IDENTITY_INVALID")
    if not SHA64.fullmatch(args.collector_sha256 or "") or not SESSION.fullmatch(args.owner_session or ""):
        raise ObservationError("AUDIT_CONTROLLER_IDENTITY_INVALID")
    if not args.run_id.isdecimal() or not args.run_attempt.isdecimal():
        raise ObservationError("AUDIT_ACTIONS_IDENTITY_INVALID")
    return {"sha": args.source_sha, "tree": args.source_tree, "collector_sha256": args.collector_sha256,
            "owner_session": args.owner_session, "run_id": args.run_id, "run_attempt": args.run_attempt}


def validate_public_receipt(receipt, expected):
    """Reject unbounded or arbitrary strings before any public artifact upload."""
    fields = {"schema", "scope", "source", "started_at", "completed_at", "host_boot_identity_sha256",
              "host_samples", "host_sample_errors", "cpu_intervals", "runtime_identities", "runtime_containers",
              "safety_samples", "filesystems", "database_metadata", "services_and_timers", "docker_images",
              "resource_reserves", "invariants", "audit_state", "host_release_admission", "host_margin_green", "blockers"}
    if not isinstance(receipt, dict) or set(receipt) != fields or receipt.get("schema") != SCHEMA or receipt.get("source") != expected:
        raise ObservationError("AUDIT_PUBLIC_SCHEMA_OR_SOURCE_INVALID")
    if receipt["host_release_admission"] != "NO_VERIFICADO" or receipt["host_margin_green"] is not False:
        raise ObservationError("AUDIT_CANNOT_GRANT_RELEASE_ADMISSION")
    if receipt["invariants"] != {"host_mutations": "NONE", "docker_exec": "NOT_CALLED", "order_routes": "NOT_CALLED",
                                  "profiles_secrets_environments": "NOT_READ", "ppi_watch": "UNTOUCHED",
                                  "recurrent_additional_usd": 0, "deploy_authorized": False}:
        raise ObservationError("AUDIT_INVARIANTS_CHANGED")
    def keys(value, required, optional=()):
        if not isinstance(value, dict) or not set(required) <= set(value) <= set(required) | set(optional):
            raise ObservationError("AUDIT_PUBLIC_NESTED_SCHEMA_INVALID")
    def observed(value, required, optional=()):
        if value.get("state") == "OBSERVED":
            keys(value, {"state", *required}, optional)
        else:
            keys(value, {"state", "error_code"}, optional)
    keys(receipt["resource_reserves"], {"disk_residual_bytes", "minimum_free_inode_percent", "memory_os_services_reserve_bytes",
                                         "candidate_memory_peak_bytes", "candidate_profile_state", "artifact_transfer_state"})
    if (receipt["resource_reserves"]["disk_residual_bytes"] != 2147483648
            or receipt["resource_reserves"]["minimum_free_inode_percent"] != 10
            or receipt["resource_reserves"]["memory_os_services_reserve_bytes"] is not None
            or receipt["resource_reserves"]["candidate_memory_peak_bytes"] is not None
            or receipt["resource_reserves"]["candidate_profile_state"] != "NO_VERIFICADO"
            or receipt["resource_reserves"]["artifact_transfer_state"] != "NO_VERIFICADO"):
        raise ObservationError("AUDIT_RESERVES_OR_PROFILE_AUTHORITY_CHANGED")
    if len(receipt["runtime_identities"]) != 2 or len(receipt["runtime_containers"]) != 2 or len(receipt["safety_samples"]) != 2:
        raise ObservationError("AUDIT_RUNTIME_BRACKETS_INCOMPLETE")
    for item in receipt["runtime_identities"]:
        keys(item, {"host_repository_sha", "repository_version", "operation_mode", "execution", "real_order_route_capability",
                    "recorded_real_order_routes", "recorded_candidate_sha", "recorded_deploy_sha", "recorded_image_id", "deploy_state_recorded_at"})
        keys(item["repository_version"], set(), {"MODE", "VERSION", "REAL_ORDER_CAPABILITY"})
    for group in receipt["runtime_containers"]:
        if [item.get("role") for item in group] != list(CONTAINERS):
            raise ObservationError("AUDIT_RUNTIME_ROLE_INVALID")
        for item in group:
            observed(item, {"role", "container_id", "image_id", "running", "started_at", "restart_count", "memory_limit_bytes", "nano_cpus",
                            "pids_limit", "readonly_rootfs", "process", "cgroup", "reported_logical_image_bytes"}, {"role"})
            if item.get("state") == "OBSERVED":
                if item["process"].get("error_code"):
                    keys(item["process"], {"pid", "error_code"})
                else:
                    keys(item["process"], {"pid", "start_ticks", "cpu_ticks", "rss_bytes"})
                cg = item["cgroup"]
                observed(cg, {"path_sha256", "memory_current", "memory_peak", "memory_max", "memory_swap_current", "memory_swap_max",
                              "pids_current", "pids_max", "cpu_quota_usec", "cpu_period_usec", "cpu_stat", "memory_stat", "memory_events"})
                if cg.get("state") == "OBSERVED":
                    for name, required in (("cpu_stat", {"usage_usec", "nr_throttled", "throttled_usec"}),
                                           ("memory_stat", {"anon", "file", "shmem", "kernel", "pagetables", "slab"}),
                                           ("memory_events", {"oom", "oom_kill", "high", "max"})):
                        keys(cg[name], required)
    for item in receipt["safety_samples"]:
        observed(item, {"mode", "real_orders_sent", "heartbeat_at", "open_mode", "write_seal"}, {"sqlite_error_code"})
        if item.get("state") == "OBSERVED":
            keys(item["write_seal"], {"abi", "filesystem_writes_denied", "wal_ignored"})
    for item in receipt["host_samples"]:
        keys(item, {"observed_at", "monotonic_ns", "online_cpu_count", "clock_ticks_per_second", "cpu_total_ticks", "cpu_idle_iowait_ticks",
                    "load_average", "memory_bytes", "process_count", "process_rss_sum_bytes", "processes_disappeared_during_sample", "rss_sum_metric"})
        keys(item["memory_bytes"], {"MemTotal", "MemAvailable", "MemFree", "Buffers", "Cached", "SReclaimable", "Shmem", "Dirty", "Writeback", "SwapTotal", "SwapFree"})
        if len(item["load_average"]) != 3:
            raise ObservationError("AUDIT_CPU_LOAD_SCHEMA_INVALID")
    if len(receipt["host_samples"]) > SAMPLE_COUNT or receipt["cpu_intervals"] != cpu_intervals(receipt["host_samples"]):
        raise ObservationError("AUDIT_SAMPLE_ARITHMETIC_INVALID")
    for name, roles in (("filesystems", ["ROOT", "DATA", "DOCKER"]), ("database_metadata", ["MAIN", "WAL", "SHM"])):
        if [item.get("role") for item in receipt[name]] != roles:
            raise ObservationError("AUDIT_METADATA_ROLE_INVALID")
    for item in receipt["filesystems"]:
        observed(item, {"role", "device_major", "device_minor", "filesystem_type", "mount_id", "allocation_unit_bytes", "total_bytes",
                        "available_bytes", "free_bytes", "inode_total", "inode_available", "inode_free_percent", "readonly"}, {"role"})
    for item in receipt["database_metadata"]:
        if item.get("state") == "ABSENT":
            keys(item, {"role", "state"})
        else:
            observed(item, {"role", "logical_bytes", "allocated_bytes", "inode", "device_major", "device_minor", "mtime_ns", "uid", "gid"}, {"role"})
    observed(receipt["services_and_timers"], {"units"})
    if receipt["services_and_timers"].get("state") == "OBSERVED":
        units = receipt["services_and_timers"]["units"]
        if sorted(item.get("Id") for item in units) != sorted(UNITS):
            raise ObservationError("AUDIT_SERVICES_ALLOWLIST_INVALID")
        for item in units:
            keys(item, SERVICE_PROPERTIES)
    observed(receipt["docker_images"], {"unique_image_count", "identity_set_sha256", "physical_unique_layer_bytes", "physical_layer_load_state"})
    def check(value, key="", depth=0):
        if depth > 10:
            raise ObservationError("AUDIT_PUBLIC_DEPTH_BOUND")
        if isinstance(value, dict):
            if len(value) > 64 or any(not isinstance(k, str) or not SAFE_ENUM.fullmatch(k) for k in value):
                raise ObservationError("AUDIT_PUBLIC_KEY_INVALID")
            for k, v in value.items():
                check(v, k, depth + 1)
        elif isinstance(value, list):
            if len(value) > 64:
                raise ObservationError("AUDIT_PUBLIC_LIST_BOUND")
            for item in value:
                check(item, key, depth + 1)
        elif isinstance(value, str):
            if not (SAFE_ENUM.fullmatch(value) or SHA40.fullmatch(value) or SHA64.fullmatch(value) or DIGEST.fullmatch(value)
                    or ISO.fullmatch(value) or (key == "owner_session" and SESSION.fullmatch(value)) or value in CONTAINERS + UNITS):
                raise ObservationError("AUDIT_PUBLIC_STRING_INVALID")
            if re.search(r"(?i)(bearer|password|secret|token|cookie|/home/|api[_-]?key)", value) and value != "NOT_READ":
                raise ObservationError("AUDIT_PUBLIC_SENSITIVE_STRING")
        elif value is None or isinstance(value, bool):
            pass
        elif isinstance(value, (int, float)):
            if not 0 <= value <= 2**64 or (isinstance(value, float) and not math.isfinite(value)):
                raise ObservationError("AUDIT_PUBLIC_NUMBER_INVALID")
        else:
            raise ObservationError("AUDIT_PUBLIC_VALUE_INVALID")
    check(receipt)
    if receipt["blockers"] != blockers(receipt):
        raise ObservationError("AUDIT_BLOCKERS_NOT_RECOMPUTED")
    return receipt


def classify_transport_error(raw: bytes, status: int):
    """Only closed first-cause labels escape; SSH messages remain private."""
    if len(raw) > 65536:
        raise ObservationError("SSH_STDERR_BOUND_EXCEEDED")
    text = raw.decode("utf-8", errors="replace")
    lower = text.lower()
    code, remote_code, linux_errno = "UNKNOWN_TRANSPORT_FAILURE", None, None
    if "DO_BINDING_MISSING" in text:
        code = "DO_BINDING_MISSING"
    elif "remote host identification has changed" in lower or re.search(r"offending .* key", lower):
        code = "SSH_HOST_KEY_MISMATCH"
    elif re.search(r"no .+ host key is known", lower):
        code = "SSH_HOST_KEY_NOT_PRETRUSTED"
    elif "host key verification failed" in lower:
        code = "SSH_HOST_KEY_VERIFICATION_FAILED"
    elif "permission denied (publickey" in lower or "no supported authentication methods" in lower:
        code = "SSH_KEY_NOT_ACCEPTED"
    elif "invalid format" in lower and "load key" in lower:
        code = "SSH_KEY_FORMAT_INVALID"
    elif re.search(r"sudo:.*(?:password is required|terminal is required|no tty present|not allowed)", lower):
        code = "REMOTE_SUDO_NONINTERACTIVE_NOT_AUTHORIZED"
    elif re.search(r"python3:.*(?:not found|no such file)|failed to run command [‘'\"]?python3", lower):
        code = "REMOTE_PYTHON_UNAVAILABLE"
    elif "could not resolve hostname" in lower:
        code = "SSH_HOST_NAME_UNRESOLVED"
    elif "connection refused" in lower or "no route to host" in lower or "network is unreachable" in lower:
        code = "SSH_TRANSPORT_UNREACHABLE"
    elif status == 124 or "timed out" in lower:
        code = "TIMEOUT"
    else:
        match = re.search(r"^RC6_HOST_READONLY_AUDIT_FAILED:([A-Z0-9_]{1,96})(?::ERRNO=(\d{1,5}))?$", text, re.MULTILINE)
        if match and match[1] in REMOTE_ERROR_CODES:
            code, remote_code = "REMOTE_AUDIT_REJECTED", match[1]
            linux_errno = number(match[2])
    return {"error_code": code, "remote_error_code": remote_code, "linux_errno": linux_errno,
            "exit_code": status, "stderr_bytes": len(raw), "stderr_sha256": hashlib.sha256(raw).hexdigest(),
            "stderr_digest_origin": "EXACT_PRIVATE_STDERR_BYTES", "raw_stderr_exported": False}


def preserve_transport_error(path: Path, status: int, expected, output: Path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ObservationError("SSH_STDERR_NOT_REGULAR")
        raw = os.read(fd, 65537)
        if len(raw) > 65536 or st.st_size > 65536:
            detail = {"error_code": "SSH_STDERR_BOUND_EXCEEDED", "remote_error_code": None, "linux_errno": None,
                      "exit_code": status, "stderr_bytes": st.st_size, "stderr_sha256": None,
                      "stderr_digest_origin": "NOT_CAPTURED_BOUND_EXCEEDED", "raw_stderr_exported": False}
        else:
            detail = classify_transport_error(raw, status)
    finally:
        os.close(fd)
    result = {"schema": "porota.rc6.host-audit-transport-error.v1", "source": expected, **detail,
              "host_release_admission": "NO_VERIFICADO", "host_margin_green": False}
    public = (json.dumps(result, sort_keys=True, indent=2) + "\n").encode()
    output.write_bytes(public)
    manifest = {"schema": "porota.rc6.host-capacity-readonly-manifest.v1", "source": expected,
                "evidence_sha256": hashlib.sha256(public).hexdigest(), "evidence_bytes": len(public),
                "host_release_admission": "NO_VERIFICADO", "host_margin_green": False}
    output.with_name("manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("source-sha", "source-tree", "collector-sha256", "run-id", "run-attempt", "owner-session"):
        parser.add_argument("--" + field, required=True)
    parser.add_argument("--sanitize-evidence", type=Path)
    parser.add_argument("--transport-stderr", type=Path)
    parser.add_argument("--transport-status", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    expected = identity(args)
    if args.transport_stderr:
        if not args.output or args.sanitize_evidence or args.transport_status is None or not 1 <= args.transport_status <= 255:
            raise ObservationError("AUDIT_TRANSPORT_ERROR_ARGUMENTS_INVALID")
        result = preserve_transport_error(args.transport_stderr, args.transport_status, expected, args.output)
        sys.stdout.write("RC6_HOST_AUDIT=RED|" + result["error_code"] + "\n")
        return 0
    if args.sanitize_evidence:
        if not args.output:
            raise ObservationError("AUDIT_SANITIZED_OUTPUT_REQUIRED")
        receipt = validate_public_receipt(json.loads(bounded_read(args.sanitize_evidence, MAX_RECEIPT_BYTES)), expected)
        raw = (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode()
        args.output.write_bytes(raw)
        manifest = {"schema": "porota.rc6.host-capacity-readonly-manifest.v1", "source": expected,
                    "evidence_sha256": hashlib.sha256(raw).hexdigest(), "evidence_bytes": len(raw),
                    "host_release_admission": "NO_VERIFICADO", "host_margin_green": False}
        args.output.with_name("manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    else:
        receipt = collect(expected)
        validate_public_receipt(receipt, expected)
        sys.stdout.write(json.dumps(receipt, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ObservationError as exc:
        code = str(exc) if re.fullmatch(r"[A-Z0-9_]{1,96}", str(exc)) else "AUDIT_STABLE_ERROR_CODE_INVALID"
        sys.stderr.write("RC6_HOST_READONLY_AUDIT_FAILED:" + code + "\n")
        raise SystemExit(1)
    except OSError as exc:
        sys.stderr.write("RC6_HOST_READONLY_AUDIT_FAILED:OBSERVATION_OSERROR:ERRNO=" + str(exc.errno or 0) + "\n")
        raise SystemExit(1)
    except (ValueError, KeyError, TypeError):
        # No arbitrary exception values or partially collected RAW are public.
        sys.stderr.write("RC6_HOST_READONLY_AUDIT_FAILED:AUDIT_STRUCTURAL_FORMAT_INVALID\n")
        raise SystemExit(1)
