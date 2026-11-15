#!/usr/bin/env python3
"""Promote one verified RC6 image in the SSH user's private rootless daemon.

No sudo, rootful Docker, package installation or database initialization is
available here.  Initial legacy quiescence belongs to porota_rootless_setup.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
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
import tarfile
import time

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

SHA = re.compile(r"[0-9a-f]{40}\Z")
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
CID = re.compile(r"[0-9a-f]{64}\Z")
ATTEMPT = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
ROLES = ("dashboard", "observer")
PROJECT = "io.porota.project"
DB_RELATIVE = Path("data/paper_v17/observer_v17.db")
# Frozen PAPER settings enable both optional workers in this release contract.
CONTROLLER_CHILDREN = ("scanner", "performance", "dynamic_shadow", "exit_reader", "notifications",
                       "candles", "intraday_scalping", "caucion_cash_sweep")
DOCKER_OPERATIONS = {
    ("image", "load"): "IMAGE_LOAD", ("image", "inspect"): "IMAGE_INSPECT",
    ("image", "ls"): "IMAGE_LS", ("image", "rm"): "IMAGE_RM",
    ("container", "create"): "CONTAINER_CREATE", ("container", "start"): "CONTAINER_START",
    ("container", "inspect"): "CONTAINER_INSPECT", ("container", "exec"): "CONTAINER_EXEC",
    ("container", "stop"): "CONTAINER_STOP", ("container", "rm"): "CONTAINER_RM",
    ("container", "update"): "CONTAINER_UPDATE", ("container", "ls"): "CONTAINER_LS",
    ("network", "ls"): "NETWORK_LS", ("network", "inspect"): "NETWORK_INSPECT",
    ("network", "rm"): "NETWORK_RM",
}


class Hold(RuntimeError):
    """Only fixed public reason codes cross the host command boundary."""


def require(condition, reason):
    if not condition:
        raise Hold(reason)


def docker_failure(args, result, returncode=None):
    """Export only fixed operation names and exit status, never command data."""
    operation = {"info": "INFO", "compose": "COMPOSE"}.get(args[0] if args else "")
    operation = operation or DOCKER_OPERATIONS.get(tuple(args[:2]), "OTHER")
    error = Hold("DOCKER_COMMAND_FAILED" if result == "EXIT_NONZERO" else "DOCKER_COMMAND_UNAVAILABLE")
    error.public_details = {"docker_failure": {"operation": operation, "result": result,
        "returncode": returncode if type(returncode) is int else None}}
    return error


def failure_evidence(exc):
    evidence = {"failure_signature": str(exc) if isinstance(exc, Hold) else "UNCLASSIFIED_HOST_FAILURE"}
    details = getattr(exc, "public_details", {})
    if isinstance(details, dict) and "docker_failure" in details:
        evidence["docker_failure"] = details["docker_failure"]
    return evidence


@dataclass(frozen=True)
class Identity:
    uid: int
    gid: int
    name: str
    home: Path

    @classmethod
    def current(cls):
        account = pwd.getpwuid(os.getuid())
        require(os.geteuid() == os.getuid() > 0 and os.getegid() == os.getgid(),
                "NONROOT_SSH_IDENTITY_REQUIRED")
        require(not account.pw_name.startswith("boca"), "FOREIGN_ACCOUNT_FORBIDDEN")
        return cls(os.getuid(), os.getgid(), account.pw_name, Path(account.pw_dir))

    @property
    def socket(self):
        return Path(f"/run/user/{self.uid}/porota-rc6-docker.sock")

    @property
    def store(self):
        return self.home / ".local/share/porota-rc6/docker"

    @property
    def state(self):
        return self.home / ".local/state/porota-rc6"

    @property
    def operator(self):
        return self.home / ".config/porota-rc6/operator"

    def environment(self):
        return {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C",
                "HOME": str(self.home), "USER": self.name, "LOGNAME": self.name,
                "XDG_RUNTIME_DIR": f"/run/user/{self.uid}",
                "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{self.uid}/bus",
                "DOCKER_CONFIG": str(self.home / ".config/porota-rc6/client"),
                "DOCKER_CLI_HINTS": "false", "COMPOSE_DISABLE_ENV_FILE": "1"}


def no_alias(path):
    path = Path(path).absolute()
    require(not any(part.is_symlink() for part in (path, *path.parents)), "PATH_ALIAS")
    return path


def read_regular(path, limit=131072):
    path = no_alias(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise Hold("REQUIRED_FILE_UNREADABLE") from exc
    try:
        meta = os.fstat(fd)
        require(stat.S_ISREG(meta.st_mode) and meta.st_nlink == 1 and meta.st_size <= limit,
                "REQUIRED_FILE_UNSAFE")
        raw = os.read(fd, limit + 1)
        require(len(raw) <= limit, "REQUIRED_FILE_OVERSIZE")
        return raw
    finally:
        os.close(fd)


def private_dir(path):
    path = no_alias(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    meta = path.stat()
    require(stat.S_ISDIR(meta.st_mode) and meta.st_uid == os.getuid() and not meta.st_mode & 0o077,
            "PRIVATE_DIRECTORY_REQUIRED")
    return path


def private_attempts_dir(identity):
    """Converge only the journal parent inside the already private state."""
    state = no_alias(identity.state)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        parent = os.open(state, flags)
        try:
            meta = os.fstat(parent)
            require(stat.S_ISDIR(meta.st_mode) and meta.st_uid == identity.uid == os.getuid() and
                    not meta.st_mode & 0o077, "ATTEMPTS_STATE_CUSTODY")
            try:
                os.mkdir("attempts", mode=0o700, dir_fd=parent)
            except FileExistsError:
                pass
            directory = os.open("attempts", flags, dir_fd=parent)
            try:
                meta = os.fstat(directory)
                require(stat.S_ISDIR(meta.st_mode) and meta.st_uid == identity.uid,
                        "ATTEMPTS_DIRECTORY_CUSTODY")
                if stat.S_IMODE(meta.st_mode) != 0o700:
                    os.fchmod(directory, 0o700)
                final = os.fstat(directory)
                require(stat.S_ISDIR(final.st_mode) and final.st_uid == identity.uid and
                        stat.S_IMODE(final.st_mode) == 0o700, "ATTEMPTS_DIRECTORY_CUSTODY")
            finally:
                os.close(directory)
        finally:
            os.close(parent)
    except OSError as exc:
        raise Hold("ATTEMPTS_DIRECTORY_CUSTODY") from exc
    return state / "attempts"


def atomic_write(path, raw, *, private=True):
    path = no_alias(path)
    if private:
        private_dir(path.parent)
    else:
        meta = path.parent.stat()
        require(stat.S_ISDIR(meta.st_mode) and meta.st_uid == os.getuid() and
                not meta.st_mode & 0o022, "RELEASE_DIRECTORY_CUSTODY")
    temporary = path.with_name(path.name + "." + os.urandom(8).hex() + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_json(path, value):
    atomic_write(path, (json.dumps(value, sort_keys=True) + "\n").encode())


def read_release_state(identity):
    """Read the current private release without loading an archive per timer."""
    current_path = identity.state / "current.json"
    current = decode(read_regular(current_path))
    meta = current_path.stat()
    require(meta.st_uid == identity.uid and not meta.st_mode & 0o077 and
            current.get("schema") == "porota.rootless.deploy.v1" and current.get("state") == "VALIDADO_RUNTIME" and
            SHA.fullmatch(str(current.get("source_sha", ""))) and SHA.fullmatch(str(current.get("source_tree", ""))) and
            IMAGE_ID.fullmatch(str(current.get("image_id", ""))) and
            current.get("operating_mode") == "PRODUCTION_PAPER" and current.get("execution_mode") == "SIMULATION" and
            type(current.get("real_orders_sent")) is int and current["real_orders_sent"] == 0,
            "CURRENT_RELEASE_UNVERIFIED")
    for directory in (identity.home, identity.state, identity.state / "releases",
                      identity.state / "releases" / current["source_sha"]):
        meta = no_alias(directory).stat()
        require(stat.S_ISDIR(meta.st_mode) and meta.st_uid == identity.uid and not meta.st_mode & 0o022,
                "PRIVATE_RELEASE_DIRECTORY_UNSAFE")
    release_path = identity.state / "releases" / current["source_sha"] / "release.json"
    raw = read_regular(release_path, 2 * 1024**2)
    meta = release_path.stat()
    require(meta.st_uid == identity.uid and not meta.st_mode & 0o077 and
            hashlib.sha256(raw).hexdigest() == current.get("release_manifest_sha256"), "PRIVATE_RELEASE_MANIFEST_CHANGED")
    release = decode(raw)
    require(release.get("schema") == "porota.release.v1" and
            all(release.get(key) == current[key] for key in ("source_sha", "source_tree", "image_id")) and
            release.get("operating_mode") == "PRODUCTION_PAPER" and release.get("execution_mode") == "SIMULATION" and
            release.get("real_order_capability") == "BLOCKED" and
            type(release.get("real_orders_sent_required")) is int and release["real_orders_sent_required"] == 0,
            "PRIVATE_RELEASE_IDENTITY_MISMATCH")
    return current, release


def persist_release(identity, release_dir, release, manifest):
    """Materialize only verified regular source files, keeping DATA in place."""
    raw = read_regular(release_dir / "release.json", 2 * 1024**2)
    require(decode(raw) == release, "RELEASE_METADATA_CHANGED")
    private_dir(identity.state)
    private_dir(identity.state / "releases")
    directory = private_dir(identity.state / "releases" / release["source_sha"])
    path = directory / "release.json"
    if path.exists():
        require(read_regular(path, 2 * 1024**2) == raw, "PERSISTED_RELEASE_DRIFT")
    manifest_rows = decode(manifest).get("files", [])
    rows = {row["path"]: row for row in manifest_rows}
    require(rows and len(rows) == len(manifest_rows), "SOURCE_MANIFEST_INVALID")
    source = private_dir(directory / "source")
    seen = set()
    with tarfile.open(release_dir / "porota-deploy-bundle-v2.tgz", "r:gz") as archive:
        for member in archive:
            if member.name == "POROTA_DEPLOY_BUNDLE_MANIFEST.json":
                continue
            require(member.isfile() and member.name in rows and member.name not in seen and
                    not Path(member.name).is_absolute() and ".." not in Path(member.name).parts and
                    member.name.split("/")[0] not in {"data", ".env", ".secrets"}, "SOURCE_MEMBER_UNSAFE")
            seen.add(member.name)
            stream = archive.extractfile(member)
            require(stream is not None and member.size <= 32 * 1024**2, "SOURCE_MEMBER_UNSAFE")
            content = stream.read(32 * 1024**2 + 1)
            row = rows[member.name]
            require(len(content) == member.size == row["bytes"] and
                    hashlib.sha256(content).hexdigest() == row["sha256"], "SOURCE_MEMBER_CHANGED")
            parent = source
            for part in Path(member.name).parts[:-1]:
                parent = private_dir(parent / part)
            destination = source / member.name
            if destination.exists():
                require(read_regular(destination, 32 * 1024**2) == content, "PERSISTED_SOURCE_DRIFT")
            else:
                atomic_write(destination, content)
    require(seen == set(rows), "SOURCE_MEMBERS_MISSING")
    atomic_write(path, raw)
    return hashlib.sha256(raw).hexdigest()


def publish_release_marker(root, release, *, validated, manifest=None):
    """Keep the existing operator rearm reader compatible and fail closed."""
    directory = no_alias(root / "data/deploy")
    directory.mkdir(mode=0o755, exist_ok=True)
    frozen = {"candidate_sha": release["source_sha"], "candidate_tree_sha": release["source_tree"],
              "image_id": release["image_id"], "real_order_capability_required": "BLOCKED",
              "paper_mode_required": "PRODUCTION_PAPER", "real_orders_sent_required": 0,
              "image_tar_sha256": release["members"]["porota-predeploy-image.tar.gz"]["sha256"],
              "build_once": True}
    marker = {"candidate_sha": release["source_sha"], "candidate_tree_sha": release["source_tree"],
              "image_id": release["image_id"], "validation_status": "VALIDATED_RUNTIME" if validated else "NO_VERIFICADO",
              "mode": "PRODUCTION_PAPER", "real_orders_sent": 0 if validated else None,
              "real_order_routes": "NOT_CALLED", "ppi_watch_untouched": True,
              "real_order_capability": "BLOCKED", "deployment_backend": "ROOTLESS_SPLIT",
              "recorded_at": datetime.now(timezone.utc).isoformat()}
    if manifest is not None:
        require(hashlib.sha256(manifest).hexdigest() ==
                release["members"]["porota-deploy-bundle-v2-manifest.json"]["sha256"], "MANIFEST_CHANGED")
        atomic_write(directory / "porota-deploy-bundle-v2-manifest.json", manifest, private=False)
    for name, value in (("porota-frozen-candidate.json", frozen), ("CURRENT_STATE_V2.json", marker)):
        atomic_write(directory / name, (json.dumps(value, sort_keys=True) + "\n").encode(), private=False)
    mode = {"schema_version": 1, "mode": "PRODUCTION_PAPER" if validated else "PENDING_RUNTIME_VALIDATION",
            "engine": "PYTHON", "execution": "SIMULATED",
            "apis": {"PPI_PRODUCTION": "MARKET_DATA_READ_ONLY", "PPI_ORDERS": "BLOCKED"},
            "changed_at": marker["recorded_at"], "telegram_notification": "NOT_SENT"}
    atomic_write(root / "data/operation_mode.json", (json.dumps(mode, sort_keys=True) + "\n").encode(), private=False)


@contextmanager
def user_lock(path):
    path = no_alias(path)
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    except OSError as exc:
        raise Hold("LOCK_UNSAFE") from exc
    try:
        meta = os.fstat(fd)
        require(stat.S_ISREG(meta.st_mode) and meta.st_uid == os.getuid() and
                meta.st_nlink == 1 and not meta.st_mode & 0o077, "LOCK_UNSAFE")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Hold("OWNER_BUSY") from exc
        yield
    finally:
        os.close(fd)


class Docker:
    def __init__(self, identity, *, run=subprocess.run):
        self.identity = identity
        self.run = run

    def command_result(self, *args, timeout=60):
        command = ("/usr/bin/docker", "--host", f"unix://{self.identity.socket}", *map(str, args))
        try:
            result = self.run(command, check=False, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=timeout,
                              env=self.identity.environment(), cwd="/")
        except subprocess.TimeoutExpired as exc:
            raise docker_failure(args, "TIMEOUT") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise docker_failure(args, "UNAVAILABLE") from exc
        require(isinstance(result.stdout, str) and len(result.stdout) <= 1048576,
                "DOCKER_RESPONSE_INVALID")
        return result

    def call(self, *args, timeout=60):
        result = self.command_result(*args, timeout=timeout)
        if result.returncode != 0:
            raise docker_failure(args, "EXIT_NONZERO", result.returncode)
        return result.stdout.strip()

    def json(self, *args):
        return decode(self.call(*args))


def decode(raw):
    try:
        return json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise Hold("JSON_EVIDENCE_INVALID") from exc


def validate_daemon(info, identity, *, classic_store=True):
    require(identity.uid > 0 and isinstance(info, dict) and
            "name=rootless" in info.get("SecurityOptions", []), "ROOTLESS_DAEMON_REQUIRED")
    require(str(info.get("CgroupVersion")) == "2" and info.get("CgroupDriver") == "systemd",
            "ROOTLESS_CGROUP_REQUIRED")
    require(info.get("DockerRootDir") == str(identity.store), "DEDICATED_DOCKER_STORE_REQUIRED")
    require(isinstance(info.get("ID"), str) and bool(info["ID"]), "DAEMON_ID_REQUIRED")
    if classic_store:
        require(info.get("Driver") == "overlay2" and isinstance(info.get("DriverStatus"), list) and
                ["driver-type", "io.containerd.snapshotter.v1"] not in info["DriverStatus"],
                "ROOTLESS_CLASSIC_IMAGE_STORE_REQUIRED")
    return info["ID"]


def validate_zero_mapping(raw, expected, *, forbidden_ids=()):
    require(expected > 0 and isinstance(raw, str) and 0 < len(raw) <= 2048, "UID_MAPPING_INVALID")
    rows = []
    try:
        for line in raw.splitlines():
            start, outside, count = map(int, line.split())
            require(min(start, outside) >= 0 and count > 0 and
                    max(start, outside) + count <= 2**32, "UID_MAPPING_INVALID")
            rows.append((start, outside, count))
    except (ValueError, TypeError) as exc:
        raise Hold("UID_MAPPING_INVALID") from exc
    require((0, expected, 1) in rows and len(rows) >= 2, "UID_MAPPING_INVALID")
    require(all(row == (0, expected, 1) or
                row[0] >= 1 and row[1] >= 65536 and all(
                    not row[1] <= identity < row[1] + row[2] for identity in forbidden_ids)
                for row in rows), "UID_MAPPING_HOST_IDENTITY_COLLISION")
    for index, row in enumerate(rows):
        for other in rows[index + 1:]:
            for offset in (0, 1):
                require(row[offset] + row[2] <= other[offset] or
                        other[offset] + other[2] <= row[offset], "UID_MAPPING_INVALID")


def validate_effective_limits(value, *, memory, pids, nanocpus):
    try:
        quota, period = map(int, value["cpu"].split())
        valid = (int(value["memory"]) == memory and int(value["pids"]) == pids and
                 quota > 0 and period > 0 and quota * 1_000_000_000 == nanocpus * period)
    except (KeyError, ValueError, TypeError, AttributeError):
        valid = False
    require(valid, "EFFECTIVE_CGROUP_LIMITS_REQUIRED")


def parse_env(raw):
    require(isinstance(raw, str) and not any(ord(c) < 32 and c not in "\n\r\t" for c in raw),
            "ENV_INVALID")
    result = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        require(separator and re.fullmatch(r"[A-Z][A-Z0-9_]*", key) and key not in result,
                "ENV_INVALID")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        result[key] = value
    return result


def runtime_environments(config, dashboard, sha, tree):
    from porota_mode_manager import paper_settings, ppi_budget_observer_selectors
    for values in (config, dashboard):
        require(str(values.get("ALLOW_REAL_ORDERS", "false")).lower() in ("false", "0", ""),
                "REAL_ORDERS_CONFIG")
        require(values.get("REAL_ORDER_CAPABILITY", "BLOCKED") == "BLOCKED" and
                values.get("EXECUTION_MODE", "SIMULATION") in ("SIMULATION", "SIMULATED"),
                "REAL_ORDERS_CONFIG")
    common = paper_settings(config)
    common.update({"PAPER_V17_DB_PATH": "/app/data/paper_v17/observer_v17.db",
                   "POROTA_BUILD_SHA": sha, "POROTA_CANDIDATE_TREE_SHA": tree,
                   "SERVER_TIMEZONE": "America/Argentina/Buenos_Aires",
                   "DASHBOARD_OPERATION_MODE": "PRODUCTION_PAPER",
                   "EXECUTION_MODE": "SIMULATION", "REAL_ORDER_CAPABILITY": "BLOCKED"})
    observer = dict(common, **ppi_budget_observer_selectors(config))
    observer.update({key: config.get(key, "") for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")})
    observer.update({"PAPER_T1_FULL_DATE_RELEASE": "true",
                     "PPI_PRODUCTION_SECRET_FILE": "/run/secrets/ppi_production.json",
                     "POROTA_CONTRACT_EVIDENCE_MODE": config.get("POROTA_CONTRACT_EVIDENCE_MODE", "ENABLED")})
    safe = {key: value for key, value in dashboard.items()
            if key.startswith(("DASHBOARD_", "COOKIE_", "SESSION_")) and
            not key.startswith(("DASHBOARD_OPERATION_MODE", "DASHBOARD_PORT", "DASHBOARD_HOST"))}
    safe.update(common)
    safe.update({"DASHBOARD_HOST": "0.0.0.0", "DASHBOARD_PORT": "8000", "DASHBOARD_REFRESH_SECONDS": "30"})
    require(bool(safe.get("DASHBOARD_ACCESS_TOKEN")), "DASHBOARD_AUTH_REQUIRED")
    return observer, safe


def database_snapshot(path):
    path = no_alias(path)
    try:
        before = path.stat()
    except FileNotFoundError as exc:
        raise Hold("DATABASE_REQUIRED") from exc
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, "DATABASE_ALIAS")
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            meta = sidecar.lstat()
            require(stat.S_ISREG(meta.st_mode) and meta.st_nlink == 1, "DATABASE_ALIAS")
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)
        try:
            connection.execute("PRAGMA query_only=ON")
            rows = connection.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchall()
            identity = connection.execute("SELECT dataset_id FROM paper_workspace WHERE id=1").fetchall()
            require(len(rows) == len(identity) == 1 and rows[0][0] == "PRODUCTION_PAPER" and
                    type(rows[0][1]) is int and rows[0][1] == 0 and bool(identity[0][0]),
                    "PAPER_DATABASE_SAFETY")
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise Hold("PAPER_DATABASE_UNREADABLE") from exc
    after = path.stat()
    require((before.st_dev, before.st_ino) == (after.st_dev, after.st_ino), "DATABASE_REPLACED")
    return {"device": before.st_dev, "inode": before.st_ino,
            "dataset_sha256": hashlib.sha256(str(identity[0][0]).encode()).hexdigest(),
            "paper_mode": "PRODUCTION_PAPER", "real_orders_sent": 0}


def required_free_bytes(release):
    members = release["members"]
    return (members["porota-predeploy-image.tar.gz"]["bytes"] + 2 * release["image_size_bytes"] +
            2 * members["porota-deploy-bundle-v2.tgz"]["bytes"] + 2147483648)


def image_present(docker, image_id):
    ids = docker.call("image", "ls", "--all", "--quiet", "--no-trunc").splitlines()
    require(all(IMAGE_ID.fullmatch(value) for value in ids), "IMAGE_INVENTORY_UNVERIFIED")
    return image_id in ids


def remove_unreferenced_image(docker, image_id):
    require(IMAGE_ID.fullmatch(image_id), "IMAGE_ID_INVALID")
    for cid in docker.call("container", "ls", "--all", "--quiet", "--no-trunc").splitlines():
        require(CID.fullmatch(cid), "CONTAINER_ID_INVALID")
        rows = decode(docker.call("container", "inspect", cid))
        require(isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict) and
                IMAGE_ID.fullmatch(str(rows[0].get("Image", ""))), "IMAGE_REFERENCE_UNVERIFIED")
        if rows[0]["Image"] == image_id:
            return "RETAINED_REFERENCED"
    # Docker rechecks references and aliases atomically; never force or prune.
    docker.call("image", "rm", image_id)
    return "REMOVED_UNREFERENCED"


def classify_runtime(row, *, now, started):
    require(isinstance(row, dict) and row.get("mode") == "PRODUCTION_PAPER" and
            type(row.get("real_orders_sent")) is int and row["real_orders_sent"] == 0,
            "PAPER_RUNTIME_SAFETY")
    def fresh(value, seconds):
        try:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return stamp.tzinfo is not None and 0 <= (now - stamp).total_seconds() <= seconds
        except (ValueError, TypeError):
            return False
    try:
        heartbeat = datetime.fromisoformat(str(row.get("heartbeat_at")).replace("Z", "+00:00"))
        new_heartbeat = heartbeat.tzinfo is not None and heartbeat >= started
    except (ValueError, TypeError):
        new_heartbeat = False
    if not fresh(row.get("heartbeat_at"), 120) or not new_heartbeat:
        return {"status": "PENDING", "source_alert": "NO_VERIFICADO"}
    auth, process = row.get("ppi_auth"), row.get("process_state")
    if process in {"RUNNING", "WAITING_MARKET", "READY_PREOPEN"} and auth == "OK":
        stale = row.get("session_state") == "MARKET_OPEN" and not fresh(row.get("last_market_data_at"), 300)
        return {"status": "READY", "source_alert": "AMBER_PPI_MARKET_DATA_STALE" if stale else "NONE"}
    if process == "DEGRADED" and auth in {"ERROR", "COOLDOWN"} and str(row.get("detail", "")).startswith((
            "Login fallo:", "PPI en espera de reintento;", "Login local bloqueado por interlock")):
        return {"status": "READY", "source_alert": "AMBER_PPI_LOGIN_UNAVAILABLE"}
    boot = row.get("session_state") in {
        "BOOT_COMMAND", "BOOT_CALENDAR", "BOOT_PPI_SECRET", "BOOT_PPI_CLIENT", "BOOT_PPI_LOGIN"}
    if (process == "STARTING" and auth in {"OK", "NOT_ATTEMPTED", "COOLDOWN"}) or (
            boot and process == "DEGRADED" and auth == "NOT_ATTEMPTED"):
        return {"status": "PENDING", "source_alert": "NO_VERIFICADO"}
    raise Hold("RUNTIME_UNCLASSIFIED_DEGRADED")


def read_controller_health(path):
    """Observe bounded controller evidence and live child PIDs inside its container."""
    try:
        health = decode(read_regular(path))
    except (OSError, Hold):
        return None
    if not isinstance(health, dict) or not isinstance(health.get("children"), dict):
        return None
    result = {key: health.get(key) for key in (
        "schema", "source_sha", "candidate_tree_sha", "recorded_at")}
    result["roles_match"] = set(health["children"]) == set(CONTROLLER_CHILDREN)
    result["children"] = {}
    for name in CONTROLLER_CHILDREN:
        child = health["children"].get(name)
        if not isinstance(child, dict):
            continue
        pid = child.get("pid")
        alive = False
        if type(pid) is int and pid > 0:
            try:
                with open(f"/proc/{pid}/stat", encoding="ascii") as process:
                    state = process.read(4096).rsplit(") ", 1)[1].split()[0]
                alive = state in {"R", "S", "D", "I"}
            except (OSError, UnicodeError, IndexError):
                pass
        result["children"][name] = {key: child.get(key) for key in ("state", "pid", "started_at")}
        result["children"][name]["alive"] = alive
    return result


def classify_controller(health, *, source_sha, source_tree, now, started):
    """Startup may retain an older file; only fresh current live children pass."""
    def pending(reason, **details):
        return {"status": "PENDING", "reason": reason, **details}
    def current_stamp(value):
        try:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return stamp if stamp.tzinfo is not None and started <= stamp <= now else None
        except (ValueError, TypeError):
            return None
    if not isinstance(health, dict) or health.get("schema") != "rc6.runtime-child-health.v1":
        return pending("CONTROLLER_HEALTH_UNAVAILABLE")
    if health.get("source_sha") != source_sha or health.get("candidate_tree_sha") != source_tree:
        return pending("CONTROLLER_SOURCE_MISMATCH")
    stamp = current_stamp(health.get("recorded_at"))
    if stamp is None or (now - stamp).total_seconds() > 120:
        return pending("CONTROLLER_HEALTH_STALE")
    children = health.get("children")
    if health.get("roles_match") is not True or not isinstance(children, dict) or set(children) != set(CONTROLLER_CHILDREN):
        return pending("CONTROLLER_CHILD_ROLES_MISMATCH")
    waiting = []
    pids = set()
    for name in CONTROLLER_CHILDREN:
        child = children[name]
        if not isinstance(child, dict):
            waiting.append(name)
            continue
        pid = child.get("pid")
        child_started = current_stamp(child.get("started_at"))
        if (child.get("state") != "RUNNING" or type(pid) is not int or pid <= 0 or pid in pids or
                child.get("alive") is not True or child_started is None or child_started > stamp):
            waiting.append(name)
        if type(pid) is int:
            pids.add(pid)
    if waiting:
        return pending("CONTROLLER_CHILDREN_NOT_RUNNING", children=sorted(waiting))
    return {"status": "READY", "entrypoint": "bv_paper_runtime.py", "recorded_at": stamp.isoformat(),
            "source_sha": source_sha, "source_tree": source_tree, "children": sorted(children)}


def validate_container(item, *, role, image_id, source_sha, source_tree, attempt, root, operator=None):
    require(role in ROLES and isinstance(item, dict), "CONTAINER_SCOPE")
    config = item.get("Config") or {}
    labels = config.get("Labels") or {}
    require(labels.get(PROJECT) == "rc6" and labels.get("io.porota.role") == role and
            labels.get("io.porota.attempt") == attempt and
            item.get("Name") == f"/porota-rc6-{role}-{attempt}" and
            CID.fullmatch(str(item.get("Id", ""))), "CONTAINER_SCOPE")
    require(item.get("Image") == image_id and config.get("Image") == image_id,
            "RUNTIME_IMAGE_MISMATCH")
    require(config.get("User") == "0:0" and not config.get("Entrypoint") and
            config.get("WorkingDir") == "/app" and
            config.get("Cmd") == ["python", "o_dashboard.py" if role == "dashboard" else "bv_paper_runtime.py"],
            "RUNTIME_COMMAND_MISMATCH")
    env = parse_env("\n".join(config.get("Env", [])))
    require(env.get("POROTA_BUILD_SHA") == source_sha and env.get("POROTA_CANDIDATE_TREE_SHA") == source_tree,
            "RUNTIME_SOURCE_MISMATCH")
    require(env.get("PAPER_V17_DB_PATH") == "/app/data/paper_v17/observer_v17.db" and
            env.get("DASHBOARD_OPERATION_MODE") == "PRODUCTION_PAPER" and
            env.get("EXECUTION_MODE") == "SIMULATION" and env.get("REAL_ORDER_CAPABILITY") == "BLOCKED",
            "RUNTIME_PAPER_CONFIG")
    settings = item.get("HostConfig") or {}
    require(settings.get("ReadonlyRootfs") is True and settings.get("Privileged") is False and
            settings.get("CapDrop") == ["ALL"] and not settings.get("CapAdd") and
            any(value in {"no-new-privileges", "no-new-privileges:true"}
                for value in settings.get("SecurityOpt", [])) and
            settings.get("NetworkMode") not in {"host", "none"} and
            not str(settings.get("NetworkMode", "")).startswith("container:") and
            settings.get("PidMode", "") != "host" and
            0 < settings.get("Memory", 0) <= 2 * 1024**3 and
            0 < settings.get("NanoCpus", 0) <= 2_000_000_000 and
            0 < settings.get("PidsLimit", 0) <= 512,
            "RUNTIME_ISOLATION")
    expected = {"/app/data": (str(root / "data"), True), "/app/operator": (str(operator), False)}
    if role == "observer":
        expected["/run/secrets/ppi_production.json"] = (str(root / ".secrets/ppi_production.json"), False)
    mounts = item.get("Mounts", [])
    binds = [m for m in mounts if m.get("Type") == "bind"]
    tmpfs_sizes = {"/tmp": 128 * 1024**2, "/home/botuser/.cache": 64 * 1024**2,
                   "/home/botuser/.config": 16 * 1024**2}
    require(len(binds) == len(expected) and {m.get("Destination") for m in binds} == set(expected) and all(
        m.get("Type") == "bind" and m.get("Destination") in expected and
        (m.get("Source"), m.get("RW")) == expected[m["Destination"]] for m in binds) and
        all(m.get("Type") == "bind" or m.get("Type") == "tmpfs" and
            m.get("Destination") in tmpfs_sizes for m in mounts),
        "RUNTIME_STORAGE_MISMATCH")
    tmpfs = settings.get("Tmpfs") or {}
    require(set(tmpfs) == set(tmpfs_sizes), "RUNTIME_TMPFS_BOUNDARY")
    for destination, options in tmpfs.items():
        flags = set(str(options).split(","))
        sizes = [re.fullmatch(r"size=(\d+)([kKmMgG]?)(?:[bB])?", flag) for flag in flags if flag.startswith("size=")]
        require({"rw", "noexec", "nosuid"} <= flags and not {"exec", "suid"} & flags and
                len(sizes) == 1 and sizes[0] is not None, "RUNTIME_TMPFS_BOUNDARY")
        size = sizes[0]
        require(int(size.group(1)) * {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[size.group(2).lower()] ==
                tmpfs_sizes[destination], "RUNTIME_TMPFS_BOUNDARY")
    ports = settings.get("PortBindings") or {}
    require(ports == ({"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8000"}]}
                      if role == "dashboard" else {}), "RUNTIME_PORT_BOUNDARY")
    require(not (item.get("State") or {}).get("OOMKilled"), "RUNTIME_OOM")
    return item["Id"]


RUNTIME_PROBE = '''import json,sqlite3
from bd_ppi_readonly_guard import ReadOnlyTransportGuard,ReadOnlyPolicyViolation
from scripts.porota_rootless_host import read_controller_health
guard=ReadOnlyTransportGuard()
for path in ('/api/1.0/orders/confirm','/api/1.0/orders/create','/api/1.0/orders/cancel'):
    try: guard.check('POST','https://clientapi.portfoliopersonal.com'+path)
    except ReadOnlyPolicyViolation: pass
    else: raise SystemExit(21)
c=sqlite3.connect('file:/app/data/paper_v17/observer_v17.db?mode=ro',uri=True,timeout=3)
c.row_factory=sqlite3.Row
c.execute('PRAGMA query_only=ON')
rows=c.execute('SELECT mode,real_orders_sent,ppi_auth,process_state,session_state,last_market_data_at,heartbeat_at,detail FROM observer_state WHERE id=1').fetchall()
assert len(rows)==1
result=dict(rows[0])
result['controller']=read_controller_health('/app/data/paper_v17/artifacts/observer_v17.db/runtime-health.json')
print(json.dumps(result))
c.close()
'''


class Host:
    def __init__(self, root, identity=None, docker=None):
        self.root = no_alias(root)
        self.identity = identity or Identity.current()
        self.docker = docker or Docker(self.identity)
        self.receipt = {"schema": "porota.rootless.deploy.v1", "state": "NO_VERIFICADO",
                        "phase": "PREFLIGHT", "real_orders_sent": None, "roles": {},
                        "cleanup": {"state": "NOT_REQUIRED", "reclaimed_bytes": 0},
                        "ppi_watch_touched": False, "boca_touched": False}

    def boundary(self, *, classic_store=True):
        for path in (self.identity.home, self.identity.socket.parent):
            meta = no_alias(path).stat()
            require(stat.S_ISDIR(meta.st_mode) and meta.st_uid == self.identity.uid and
                    not meta.st_mode & 0o022, "ROOTLESS_RUNTIME_DIRECTORY")
        meta = self.identity.socket.lstat()
        require(stat.S_ISSOCK(meta.st_mode) and meta.st_uid == self.identity.uid,
                "ROOTLESS_SOCKET_REQUIRED")
        return validate_daemon(self.docker.json("info", "--format", "{{json .}}"), self.identity,
                               classic_store=classic_store)

    def inspect(self, cid):
        require(CID.fullmatch(cid), "CONTAINER_ID_INVALID")
        rows = self.docker.json("container", "inspect", cid)
        require(isinstance(rows, list) and len(rows) == 1 and rows[0].get("Id") == cid,
                "EXACT_CONTAINER_REQUIRED")
        return rows[0]

    def check_role(self, document, role):
        cid = document["roles"][role]
        item = self.inspect(cid)
        validate_container(item, role=role, image_id=document["image_id"],
                           source_sha=document["source_sha"], source_tree=document["source_tree"],
                           attempt=document["attempt"], root=self.root, operator=self.identity.operator)
        return item

    def check_inventory(self, current, *, allow_empty_migration=False):
        known = set(current.get("roles", {}).values()) if current else set()
        observed = set(self.docker.call("container", "ls", "--all", "--quiet", "--no-trunc").splitlines())
        pending = current.get("cleanup_pending") if current else None
        if pending:
            pending["roles"] = {role: cid for role, cid in pending["roles"].items() if cid in observed}
            known.update(pending["roles"].values())
        require(observed == known, "UNOWNED_ROOTLESS_CONTAINERS")
        if current:
            require(set(current["roles"]) == set(ROLES) or
                    not current["roles"] and (current.get("legacy_quiescence", {}).get("state") == "QUIESCENT" or
                    allow_empty_migration and current.get("migration_pending") is True),
                    "CURRENT_PAIR_INCOMPLETE")
            for role in current["roles"]:
                self.check_role(current, role)
        if pending:
            for role in pending["roles"]:
                require(self.check_role(pending, role)["State"].get("Running") is False,
                        "RETIRED_RUNTIME_RESTARTED")

    def resume_cleanup(self, current, current_path):
        pending = current.get("cleanup_pending")
        if not pending:
            return
        self.cleanup_pair(pending)
        if pending["image_id"] != current["image_id"] and image_present(self.docker, pending["image_id"]):
            remove_unreferenced_image(self.docker, pending["image_id"])
        current.pop("cleanup_pending")
        current["cleanup"] = {"state": "COMPLETE", "recovered_previous_cleanup": True}
        write_json(current_path, current)

    def effective_mapping_probe(self, image_id, attempt):
        cid = self.docker.call("container", "create", "--name", f"porota-rc6-probe-{attempt}",
            "--label", f"{PROJECT}=rc6", "--label", f"io.porota.attempt={attempt}",
            "--user", "0:0", "--read-only", "--network", "none", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true", "--pids-limit", "16", "--memory", "128m",
            "--cpus", "0.25", "--no-healthcheck", "--entrypoint", "python", image_id,
            "-c", "import time;time.sleep(60)")
        require(CID.fullmatch(cid), "PROBE_BIRTH_UNVERIFIED")
        primary_failed = False
        try:
            self.docker.call("container", "start", cid)
            before = self.inspect(cid)
            require(before.get("Image") == image_id and before.get("State", {}).get("Running") is True,
                    "PROBE_NOT_RUNNING")
            pid = before["State"].get("Pid")
            require(type(pid) is int and pid > 0, "PROBE_PID_UNVERIFIED")
            import select
            descriptor = os.pidfd_open(pid)
            try:
                poll = select.poll()
                poll.register(descriptor, select.POLLIN)
                for kind, expected, protected in (
                        ("uid_map", self.identity.uid, [item.pw_uid for item in pwd.getpwall()]),
                        ("gid_map", self.identity.gid, [item.gr_gid for item in grp.getgrall()])):
                    validate_zero_mapping(read_regular(Path(f"/proc/{pid}/{kind}"), 2048).decode(), expected,
                                          forbidden_ids=protected)
                require(os.stat(f"/proc/{pid}/ns/user").st_ino != os.stat("/proc/self/ns/user").st_ino,
                        "USER_NAMESPACE_REQUIRED")
                proof = self.docker.json("container", "exec", cid, "python", "-c",
                    "import os,json,pathlib; p=pathlib.Path('/sys/fs/cgroup'); print(json.dumps({'uid':os.getuid(),'gid':os.getgid(),'cpu':(p/'cpu.max').read_text().strip(),'memory':(p/'memory.max').read_text().strip(),'pids':(p/'pids.max').read_text().strip()}))")
                validate_effective_limits(proof, memory=128 * 1024**2, pids=16, nanocpus=250_000_000)
                require(proof.get("uid") == 0 and proof.get("gid") == 0 and not poll.poll(0) and
                        self.inspect(cid)["State"]["Pid"] == pid, "EFFECTIVE_UID_MAPPING_CHANGED")
            finally:
                os.close(descriptor)
            return {"container_uid": 0, "host_uid": self.identity.uid, "host_gid": self.identity.gid,
                    "effective_cgroup_limits": "VERIFIED"}
        except BaseException:
            primary_failed = True
            raise
        finally:
            # Only the CID returned by this create, never a name or a broad rm.
            try:
                current = self.inspect(cid)
                require(current.get("Image") == image_id and
                        (current.get("Config", {}).get("Labels") or {}).get("io.porota.attempt") == attempt,
                        "PROBE_CLEANUP_IDENTITY")
                if current.get("State", {}).get("Running"):
                    self.docker.call("container", "stop", "--time", "5", cid)
                self.docker.call("container", "rm", cid)
            except Exception as cleanup_exc:
                self.receipt["probe_cleanup_failure"] = failure_evidence(cleanup_exc)
                if not primary_failed:
                    raise

    def prepare_environment(self, directory, release):
        config = parse_env(read_regular(self.root / ".env").decode())
        dashboard = parse_env(read_regular(self.root / "data/diagnosticos/dashboard_preview_v1633.env").decode())
        observer, dashboard = runtime_environments(config, dashboard, release["source_sha"], release["source_tree"])
        for role, values in (("observer", observer), ("dashboard", dashboard)):
            require(all("\n" not in v and "\r" not in v for v in values.values()), "ENV_INVALID")
            atomic_write(directory / (role + ".env"),
                         ("\n".join(f"{key}={value}" for key, value in values.items()) + "\n").encode())
        secret = self.root / ".secrets/ppi_production.json"
        require(secret.is_file() and not no_alias(secret).stat().st_mode & 0o077 and os.access(secret, os.R_OK),
                "PPI_SECRET_ACCESS_REQUIRED")
        operator = no_alias(self.identity.operator)
        meta = operator.stat()
        require(stat.S_ISDIR(meta.st_mode) and meta.st_uid == self.identity.uid and not meta.st_mode & 0o077,
                "OPERATOR_CUSTODY_REQUIRED")
        require(os.access(self.root / "data", os.W_OK) and os.access(self.root / DB_RELATIVE, os.W_OK),
                "EXISTING_DATA_WRITE_ACCESS_REQUIRED")
        values = {"POROTA_ROOT": str(self.root), "POROTA_ENV_DIR": str(directory),
                  "POROTA_OPERATOR_DIR": str(operator), "POROTA_IMAGE_ID": release["image_id"],
                  "POROTA_ATTEMPT": self.receipt["attempt"]}
        require(not any("\n" in value or "\r" in value or "$" in value for value in values.values()), "COMPOSE_PATH_INVALID")
        atomic_write(directory / "compose.env", ("\n".join(f"{k}={v}" for k, v in values.items()) + "\n").encode())

    def compose(self, release_dir, directory, *args):
        source = release_dir.parent / "source/compose.rootless.yml"
        require(source.is_file() and not source.is_symlink(), "COMPOSE_SOURCE_REQUIRED")
        return self.docker.call("compose", "--project-name", "porota-rc6-" + self.receipt["attempt"].lower(),
            "--file", source, "--env-file", directory / "compose.env", *args, timeout=180)

    def retire(self, document):
        for role in ("observer", "dashboard"):
            if role not in document["roles"]:
                continue
            item = self.check_role(document, role)
            # A quiesced predecessor must not return after a daemon/host boot.
            self.docker.call("container", "update", "--restart=no", item["Id"])
            if item["State"].get("Running"):
                self.docker.call("container", "stop", "--time", "30", item["Id"], timeout=45)
            require(self.check_role(document, role)["State"].get("Running") is False, "RUNTIME_STOP_UNVERIFIED")

    def cleanup_pair(self, document):
        for role in ("observer", "dashboard"):
            if role not in document["roles"]:
                continue
            item = self.check_role(document, role)
            require(item["State"].get("Running") is False, "ACTIVE_CONTAINER_CLEANUP_FORBIDDEN")
            self.docker.call("container", "rm", item["Id"])
        project = "porota-rc6-" + document["attempt"].lower()
        for network in self.docker.call("network", "ls", "--quiet", "--no-trunc", "--filter",
                                        "label=com.docker.compose.project=" + project).splitlines():
            require(CID.fullmatch(network), "NETWORK_ID_INVALID")
            rows = self.docker.json("network", "inspect", network)
            require(len(rows) == 1 and rows[0].get("Labels", {}).get("com.docker.compose.project") == project and
                    not rows[0].get("Containers"), "NETWORK_CLEANUP_UNVERIFIED")
            self.docker.call("network", "rm", network)

    def discover_attempt(self, document):
        """Recover partial compose creation by immutable attempt labels."""
        roles = {}
        for cid in self.docker.call("container", "ls", "--all", "--quiet", "--no-trunc",
                "--filter", f"label={PROJECT}=rc6", "--filter",
                "label=io.porota.attempt=" + document["attempt"]).splitlines():
            item = self.inspect(cid)
            role = (item.get("Config", {}).get("Labels") or {}).get("io.porota.role")
            require(role in ROLES and role not in roles, "ATTEMPT_CLEANUP_AMBIGUOUS")
            validate_container(item, role=role, image_id=document["image_id"],
                source_sha=document["source_sha"], source_tree=document["source_tree"],
                attempt=document["attempt"], root=self.root, operator=self.identity.operator)
            roles[role] = cid
        return dict(document, roles=roles)

    def postflight(self, document, started, *, budget=1200, sleep=time.sleep, clock=time.monotonic, guard=None):
        deadline = clock() + budget
        while clock() < deadline:
            if guard:
                guard()
            dashboard = self.check_role(document, "dashboard")
            observer = self.check_role(document, "observer")
            require(all(item["State"].get("Running") is True for item in (dashboard, observer)), "RUNTIME_PROCESS_STOPPED")
            row = self.docker.json("container", "exec", document["roles"]["observer"],
                                   "python", "-c", RUNTIME_PROBE)
            now = datetime.now(timezone.utc)
            status = classify_runtime(row, now=now, started=started)
            controller = classify_controller(row.get("controller"), source_sha=document["source_sha"],
                source_tree=document["source_tree"], now=now, started=started)
            self.receipt["controller"] = controller
            health = dashboard["State"].get("Health", {}).get("Status")
            if health == "healthy" and status["status"] == "READY" and controller["status"] == "READY":
                return {"dashboard": "healthy", "observer": "running", "orders_guard": "BLOCKED",
                        "source_alert": status["source_alert"], "paper_readiness": "FAIL_CLOSED_PER_INSTRUMENT"}
            sleep(5)
        raise Hold("RUNTIME_STARTUP_TIMEOUT")

    def deploy(self, release_dir, source_sha, source_tree, attempt, *, transition=None, guard=None):
        from scripts.porota_release import verify_release
        require(SHA.fullmatch(source_sha) and SHA.fullmatch(source_tree) and ATTEMPT.fullmatch(attempt),
                "RELEASE_COORDINATES_INVALID")
        release_dir = no_alias(release_dir)
        release = verify_release(release_dir, expected_sha=source_sha, expected_tree=source_tree)
        manifest = read_regular(release_dir / "porota-deploy-bundle-v2-manifest.json", 2 * 1024**2)
        require(hashlib.sha256(manifest).hexdigest() ==
                release["members"]["porota-deploy-bundle-v2-manifest.json"]["sha256"], "MANIFEST_CHANGED")
        require(IMAGE_ID.fullmatch(release["image_id"]), "IMAGE_ID_INVALID")
        self.receipt.update({key: release[key] for key in ("source_sha", "source_tree", "image_id")})
        self.receipt["attempt"] = attempt
        private_dir(self.identity.state)
        with user_lock(self.identity.state / "deploy.lock"):
            if guard:
                guard()
            daemon_id = self.boundary()
            self.receipt["daemon_id"] = daemon_id
            current_path = self.identity.state / "current.json"
            current = decode(read_regular(current_path)) if current_path.exists() else None
            require(current is not None or transition is not None, "INITIAL_MIGRATION_REQUIRED")
            if current:
                require(transition is not None or current.get("migration_pending") is not True,
                        "INITIAL_MIGRATION_INCOMPLETE_USE_MIGRATE")
                require(current.get("daemon_id") == daemon_id and
                        (current.get("state") == "VALIDADO_RUNTIME" or
                         current.get("state") == "NO_VERIFICADO" and (current.get("fix_forward_ready") is True or
                         transition is not None and current.get("migration_pending") is True and
                         current.get("cleanup", {}).get("state") == "COMPLETE" and not current.get("roles"))),
                        "CURRENT_STATE_UNVERIFIED")
            self.check_inventory(current, allow_empty_migration=transition is not None)
            if transition is not None:
                self.receipt["migration_pending"] = True
            if current:
                self.resume_cleanup(current, current_path)
            directory = private_attempts_dir(self.identity) / attempt
            require(not directory.exists(), "ATTEMPT_EXISTS_READ_ONLY_RCA_REQUIRED")
            private_dir(directory)
            before = database_snapshot(self.root / DB_RELATIVE)
            self.receipt["database"] = before
            self.prepare_environment(directory, release)
            disk_before = shutil.disk_usage(self.identity.store).free
            data_free = shutil.disk_usage(self.root / "data").free
            required = required_free_bytes(release)
            self.receipt["storage"] = {"free_before": disk_before, "required_before_load": required,
                "data_free_before": data_free, "data_required_reserve": 2147483648,
                "docker_device": self.identity.store.stat().st_dev,
                "data_device": (self.root / "data").stat().st_dev}
            require(disk_before >= required, "ROOTLESS_DISK_CAPACITY")
            require(data_free >= 2147483648, "DATA_DISK_CAPACITY")
            self.receipt["release_manifest_sha256"] = persist_release(self.identity, release_dir, release, manifest)
            journal = directory / "receipt.json"
            write_json(journal, self.receipt)
            image_was_present = image_present(self.docker, release["image_id"])
            transitioned = False
            committed = False
            try:
                self.receipt["phase"] = "LOAD"
                self.docker.call("image", "load", "--input", release_dir / "porota-predeploy-image.tar.gz", timeout=600)
                self.receipt["phase"] = "IMAGE_INSPECT"
                images = self.docker.json("image", "inspect", release["image_id"])
                require(len(images) == 1 and images[0].get("Id") == release["image_id"], "LOADED_IMAGE_ID_MISMATCH")
                env = parse_env("\n".join(images[0].get("Config", {}).get("Env", [])))
                require(env.get("POROTA_BUILD_SHA") == source_sha and env.get("POROTA_CANDIDATE_TREE_SHA") == source_tree,
                        "LOADED_IMAGE_SOURCE_MISMATCH")
                require(shutil.disk_usage(self.identity.store).free >= 2147483648, "ROOTLESS_POSTLOAD_RESERVE")
                require(shutil.disk_usage(self.root / "data").free >= 2147483648, "DATA_POSTLOAD_RESERVE")
                self.receipt["phase"] = "MAPPING_PROBE"
                self.receipt["uid_mapping"] = self.effective_mapping_probe(release["image_id"], attempt)
                self.receipt["phase"] = "CREATE"
                self.compose(release_dir, directory, "create", "--no-build", "--pull", "never")
                for role in ROLES:
                    cid = self.compose(release_dir, directory, "ps", "--all", "--quiet", role)
                    require(CID.fullmatch(cid), "CANDIDATE_BIRTH_UNVERIFIED")
                    self.receipt["roles"][role] = cid
                    self.check_role(self.receipt, role)
                    write_json(journal, self.receipt)
                require(database_snapshot(self.root / DB_RELATIVE) == before, "DATABASE_CHANGED_IDENTITY")
                if guard:
                    guard()
                self.receipt["phase"] = "QUIESCE"
                transitioned = True  # A partially failed transition is still a mutation attempt.
                write_json(journal, self.receipt)
                publish_release_marker(self.root, release, validated=False, manifest=manifest)
                if transition:
                    # Producer and consumer share this process, user lock and
                    # the bootstrap's root-owned global lock. No supplied flag.
                    self.receipt["legacy_quiescence"] = transition()
                    if current:
                        # A resumed migration may have a validated private pair
                        # but unrestored timers. Fix forward retires that exact
                        # pair before starting its replacement, never two writers.
                        self.retire(current)
                elif current:
                    self.retire(current)
                self.receipt["phase"] = "START"
                write_json(journal, self.receipt)
                started = datetime.now(timezone.utc)
                for role in ROLES:
                    if guard:
                        guard()
                    self.check_role(self.receipt, role)
                    self.docker.call("container", "start", self.receipt["roles"][role])
                self.receipt["phase"] = "POSTFLIGHT"
                self.receipt["health"] = self.postflight(self.receipt, started, guard=guard)
                if guard:
                    guard()
                require(database_snapshot(self.root / DB_RELATIVE) == before, "DATABASE_CHANGED_IDENTITY")
                self.receipt.update({"state": "VALIDADO_RUNTIME", "real_orders_sent": 0,
                                     "operating_mode": "PRODUCTION_PAPER", "execution_mode": "SIMULATION"})
                if current:
                    self.receipt["cleanup_pending"] = {key: current[key] for key in
                        ("source_sha", "source_tree", "image_id", "attempt", "roles")}
                publish_release_marker(self.root, release, validated=True, manifest=manifest)
                write_json(current_path, self.receipt)
                committed = True
                self.receipt["phase"] = "CLEANUP"
                cleanup_before = shutil.disk_usage(self.identity.store).free
                if current:
                    self.resume_cleanup(self.receipt, current_path)
                cleanup_after = shutil.disk_usage(self.identity.store).free
                self.receipt["cleanup"] = {"state": "COMPLETE",
                    "free_bytes_before": cleanup_before, "free_bytes_after": cleanup_after,
                    "reclaimed_bytes": max(0, cleanup_after - cleanup_before)}
                self.receipt["storage"]["data_free_after"] = shutil.disk_usage(self.root / "data").free
                self.receipt["phase"] = "COMPLETE"
                write_json(current_path, self.receipt)
                write_json(journal, self.receipt)
                return self.receipt
            except Exception as exc:
                self.receipt.update({"state": "VALIDADO_RUNTIME" if committed else "NO_VERIFICADO", **failure_evidence(exc),
                                     "transition_started": transitioned, "action": "FIX_FORWARD_ONLY"})
                self.receipt["cleanup"] = {"state": "INCOMPLETE", "reclaimed_bytes": 0}
                if committed:
                    # Housekeeping failure cannot tear down the validated replacement.
                    write_json(current_path, self.receipt)
                else:
                    try:
                        cleanup_before = shutil.disk_usage(self.identity.store).free
                        born = self.discover_attempt(self.receipt)
                        self.retire(born)
                        self.cleanup_pair(born)
                        require(not self.discover_attempt(self.receipt)["roles"], "ATTEMPT_CLEANUP_INCOMPLETE")
                        if not image_was_present and image_present(self.docker, release["image_id"]):
                            removal = remove_unreferenced_image(self.docker, release["image_id"])
                            require(removal != "RETAINED_REFERENCED", "ATTEMPT_IMAGE_STILL_REFERENCED")
                        cleanup_after = shutil.disk_usage(self.identity.store).free
                        self.receipt["cleanup"] = {"state": "COMPLETE", "free_bytes_before": cleanup_before,
                            "free_bytes_after": cleanup_after, "reclaimed_bytes": max(0, cleanup_after - cleanup_before)}
                    except Exception as cleanup_exc:
                        self.receipt["cleanup"].update(failure_evidence(cleanup_exc))
                    if transitioned:
                        publish_release_marker(self.root, release, validated=False, manifest=manifest)
                        failed = dict(current or self.receipt)
                        if not current:
                            failed["roles"] = {}
                        failed.update({"state": "NO_VERIFICADO", "failure_signature": self.receipt["failure_signature"],
                            "fix_forward_ready": self.receipt["cleanup"]["state"] == "COMPLETE" and
                                bool(current or self.receipt.get("legacy_quiescence", {}).get("state") == "QUIESCENT")})
                        write_json(current_path, failed)
                write_json(journal, self.receipt)
                if isinstance(exc, Hold):
                    raise
                raise Hold(self.receipt["failure_signature"]) from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("inspect", "deploy"))
    parser.add_argument("--root", type=Path, default=Path("/opt/porota-trading"))
    parser.add_argument("--release-dir", type=Path)
    parser.add_argument("--source-sha")
    parser.add_argument("--source-tree")
    parser.add_argument("--attempt")
    args = parser.parse_args(argv)
    manager = None
    try:
        manager = Host(args.root)
        if args.operation == "inspect":
            result = {"schema": "porota.rootless.runtime-inspect.v1", "state": "NO_VERIFICADO",
                      "daemon_id": manager.boundary(), "host_mutations": 0}
        else:
            require(all((args.release_dir, args.source_sha, args.source_tree, args.attempt)), "RELEASE_ARGUMENTS_REQUIRED")
            result = manager.deploy(args.release_dir, args.source_sha, args.source_tree, args.attempt)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        result = manager.receipt if manager else {"state": "BLOQUEADO", "phase": "IDENTITY"}
        result.update(failure_evidence(exc))
        print(json.dumps(result, sort_keys=True))
        return 79


if __name__ == "__main__":
    raise SystemExit(main())
