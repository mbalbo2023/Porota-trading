#!/usr/bin/env python3
"""A630: bounded PPI Current+Book sample, no deployment and no trading calls.

This source is streamed via stdin to (a) a PRIVATE read-only host mount namespace
for exact PPI catalog identity and (b) the existing pinned PAPER Docker container.
Normal mode uses the existing shared RuntimePPIBudget. Only explicit manual
pilot mode may instead create a separate bounded diagnostic SQLite ledger while
holding the native bootstrap lock for the absent runtime budget. No production
budget SQLite or orders are written. The public artifact carries sanitized
status/hashes, never provider payload.
"""

from __future__ import annotations

import base64
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import fcntl
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import pwd
import re
import resource
import sqlite3
import stat
import subprocess
import sys
from urllib.parse import parse_qsl, quote, urlsplit
import uuid
from zoneinfo import ZoneInfo

SCHEMA = "rc6.a630.ppi.current-book.sample.v1"
# Additive, success-only evidence fields. Historical v1 receipts remain valid;
# failed captures retain the original v1 shape and do not expose partial clocks.
PER_GET_CLOCK_FIELDS = frozenset({
    "current_received_at_utc", "book_received_at_utc", "pair_completed_at_utc",
})
# These are exact deployed *candidate* coordinates, NOT the merged product
# branch SHA and NOT the separately audited BYMA calendar revision.
ROOTLESS_CANDIDATE_SHA = "96c626dc09a045f75f5e743c030e8f8228783a54"
ROOTLESS_CANDIDATE_TREE = "b82e85e8388463d35b6946adcaf3a562c5ad5dfb"
ROOTLESS_IMAGE_ID = "sha256:2f0db4052d4000f7647428591eece1f26168ed173363daa4089f1762032b9563"
ROOTLESS_UID = 1000
ROOTLESS_SCHEMA = "rc6.a630.rootless.capture.v1"
# Historical A630 v1 receipts retain this field name for compatibility; the
# identity is the candidate embedded into the exact already-deployed image.
PRODUCT_SHA = ROOTLESS_CANDIDATE_SHA
DB = Path("/opt/porota-trading/data/paper_v17/observer_v17.db")
PPI_HOST = "clientapi.portfoliopersonal.com"
MAX_BODY = 128 * 1024
MAX_LEVELS = 200
MAX_AGE_MS = 120_000
MAX_EXIT_ROUND_AGE_MS = 300_000
SHA256 = re.compile(r"[a-f0-9]{64}\Z")
CID64 = re.compile(r"[a-f0-9]{64}\Z")
ATTEMPT = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
PPI_TYPES = {"ACCIONES": "ACCIONES", "BONOS": "BONOS"}
# One reviewed target per intraday window. Catalog must prove the FULL 5-key.
SLOTS = {
    "1045": ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS"),
    "1120": ("AL30", "BONOS", "BYMA", "ARS", "A-24HS"),
    "1430": ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS"),
    "1630": ("AL30", "BONOS", "BYMA", "ARS", "A-24HS"),
}
REASONS = frozenset({
    "NOT_REQUESTED", "HOST_RO_MOUNT_REQUIRED", "DB_PAPER_IDENTITY_HOLD",
    "PPI_PRIMARY_FIVE_KEY_NOT_UNIQUE", "PPI_PRIMARY_SOURCE_NOT_ATTESTED",
    "HOST_PREFLIGHT_OR_RUNTIME_HOLD", "SLOT_OR_DAY_HOLD",
    "PRODUCT_IMAGE_OR_MODE_HOLD", "PPI_KEYPAIR_UNAVAILABLE",
    "SHARED_BUDGET_OR_EXIT_RESERVE_NOT_ATTESTED", "ATOMIC_LEASE_DENIED",
    "PPI_BUDGET_SIDECAR_ABSENT", "PPI_BUDGET_SIDECAR_PATH_UNSAFE",
    "PPI_BUDGET_SNAPSHOT_NOT_OBSERVED", "PPI_RETENTION_NOT_READY",
    "PPI_EXIT_LOWER_SUSPENDED", "PPI_EXIT_ROUND_NOT_ATTESTED",
    "PPI_EXIT_ROUND_STALE_OR_CLOCK_INVALID",
    "PPI_BUDGET_RUNTIME_BACKEND_MISMATCH", "PPI_BUDGET_PROBE_UNAVAILABLE",
    "PPI_LOCAL_PILOT_PAPER_LEDGER_HOLD", "PPI_LOCAL_PILOT_STATE_UNAVAILABLE",
    "PPI_LOCAL_PILOT_SLOT_OR_SESSION_HOLD", "PPI_LOCAL_PILOT_RUNTIME_BUDGET_HOLD",
    "PPI_LOCAL_PILOT_PAPER_ADMISSION_HOLD",
    "PPI_LOCAL_PILOT_ALREADY_USED",
    "PPI_HTTP_429", "PPI_HTTP_401", "PPI_HTTP_403", "PPI_SESSION_INVALID",
    "PROVIDER_GET_SCOPE_OR_COUNT_INVALID", "PPI_PROVIDER_OR_AUTH_FAILED",
    "PROVIDER_RESPONSE_SHAPE_INVALID", "PPI_NATIVE_CLOCK_INVALID",
    "PPI_NATIVE_CLOCK_STALE_OR_FUTURE", "BODY_BUDGET_OR_DIGEST_INVALID",
    "PPI_NATIVE_STALE_POSTCLOSE_DIAGNOSTIC_NO_GO",
    "PPI_GET_RECEIPT_SDK_ONLY_NO_CAUSAL_PIT", "RECEIPT_INVALID",
    "ROOTLESS_PIN_MISMATCH", "ROOTLESS_SSH_IDENTITY_HOLD",
    "ROOTLESS_PRIVATE_PATH_HOLD", "ROOTLESS_RELEASE_CUSTODY_HOLD",
    "ROOTLESS_STATE_DRIFT_HOLD", "ROOTLESS_SOCKET_HOLD",
    "ROOTLESS_DAEMON_HOLD", "ROOTLESS_OBSERVER_HOLD",
    "ROOTLESS_DOCKER_TRANSPORT_HOLD", "ROOTLESS_EXEC_RECEIPT_HOLD",
})


def target_for_slot(slot):
    if slot not in SLOTS:
        raise ValueError("SLOT_OR_DAY_HOLD")
    return SLOTS[slot]


def decode_target(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 1024:
        raise ValueError("PPI_PRIMARY_FIVE_KEY_NOT_UNIQUE")
    try:
        raw = base64.b64decode(value, validate=True)
        if len(raw) > 256:
            raise ValueError("PPI_PRIMARY_FIVE_KEY_NOT_UNIQUE")
        data = json.loads(raw)
        if not isinstance(data, list) or len(data) != 5 or not all(
                isinstance(item, str) for item in data):
            raise ValueError("PPI_PRIMARY_FIVE_KEY_NOT_UNIQUE")
        target = tuple(data)
        if target not in SLOTS.values():
            raise ValueError("PPI_PRIMARY_FIVE_KEY_NOT_UNIQUE")
        return target
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("PPI_PRIMARY_FIVE_KEY_NOT_UNIQUE") from None


def _private_readonly_mount(path):
    chosen = (-1, False)
    try:
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            fields = line.split(" - ", 1)[0].split()
            if len(fields) < 6:
                continue
            at = fields[4].replace("\\040", " ")
            if at != "/" and path != Path(at) and not str(path).startswith(at.rstrip("/") + "/"):
                continue
            if len(at) > chosen[0]:
                chosen = (len(at), "ro" in fields[5].split(","))
    except (OSError, UnicodeError):
        return False
    return chosen[0] >= len("/opt/porota-trading/data") and chosen[1]


def _paper_db_identity(conn):
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(paper_workspace)")}
    if not {"id", "namespace", "origin", "imported_legacy", "dataset_id"} <= columns:
        return False
    rows = conn.execute("SELECT id,namespace,origin,imported_legacy,dataset_id "
                        "FROM paper_workspace LIMIT 2").fetchall()
    if len(rows) != 1:
        return False
    try:
        row = rows[0]
        return (row[:4] == (1, "POROTA_PAPER_V17_FRESH", "FRESH_EMPTY", 0)
                and str(uuid.UUID(row[4])) == row[4])
    except (TypeError, ValueError, AttributeError):
        return False


def _catalog_primary_identity(conn, target):
    """Catalog identity cannot infer PPI authority from plain name/market alone."""
    tables = {str(row[0]) for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "financial_instrument_catalog" not in tables:
        return False, "PPI_PRIMARY_SOURCE_NOT_ATTESTED"
    columns = {str(row[1]) for row in conn.execute(
        "PRAGMA table_info(financial_instrument_catalog)")}
    required = {"ticker", "instrument_type", "market", "currency",
                "settlement", "settlement_source"}
    if not required <= columns:
        return False, "PPI_PRIMARY_SOURCE_NOT_ATTESTED"
    # Mirror the reviewed A595 product pre-GET rule. A complementary-only
    # IOL/BYMA sibling MUST NOT veto a unique PPI identity, but a second PPI
    # sibling (even stale) does veto the 3-field SDK request. REQUEST_CANDIDATE
    # is selection authority, NOT independent native source PIT attestation.
    rows = conn.execute("SELECT ticker,instrument_type,market,currency,settlement,settlement_source "
        "FROM financial_instrument_catalog WHERE ticker=? AND instrument_type=? "
        "AND settlement=? AND settlement_source IN ('PPI_FIELD','REQUEST_CANDIDATE') "
        "LIMIT 3", (target[0], target[1], target[4])).fetchall()
    if not rows:
        return False, "PPI_PRIMARY_SOURCE_NOT_ATTESTED"
    if len(rows) != 1 or tuple(rows[0][:5]) != target:
        return False, "PPI_PRIMARY_FIVE_KEY_NOT_UNIQUE"
    if rows[0][5] not in {"PPI_FIELD", "REQUEST_CANDIDATE"}:
        return False, "PPI_PRIMARY_SOURCE_NOT_ATTESTED"
    return True, "PPI_PRIMARY_FIVE_KEY_UNIQUE"


def host_preflight(slot, db=DB, *, enforce_mount=True):
    report = {"schema": SCHEMA, "phase": "HOST_PREFLIGHT", "state": "NO_VERIFICADO",
              "reason": "HOST_PREFLIGHT_OR_RUNTIME_HOLD", "ppi_gets": 0,
              "target": None, "db_opened": False, "sqlite_ro_mount_protected": False,
              "paper_db_identity_verified": False, "ppi_primary_5key_unique": False,
              "orders_sent": 0, "source_pit_verified": False}
    try:
        target = target_for_slot(slot)
        if not db.is_absolute() or db.is_symlink() or not (db.exists() and db.is_file()):
            raise ValueError("DB_PAPER_IDENTITY_HOLD")
        if enforce_mount and not _private_readonly_mount(db):
            raise ValueError("HOST_RO_MOUNT_REQUIRED")
        report["sqlite_ro_mount_protected"] = enforce_mount
        uri = "file:" + quote(str(db), safe="/") + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=0.25, isolation_level=None) as conn:
            conn.execute("PRAGMA query_only=ON")
            report["db_opened"] = True
            if not _paper_db_identity(conn):
                raise ValueError("DB_PAPER_IDENTITY_HOLD")
            report["paper_db_identity_verified"] = True
            okay, reason = _catalog_primary_identity(conn, target)
            if not okay:
                raise ValueError(reason)
        report.update(state="IDENTITY_ONLY_NO_GET", reason="PPI_PRIMARY_FIVE_KEY_UNIQUE",
                      target=list(target), ppi_primary_5key_unique=True)
    except (sqlite3.Error, OSError, ValueError, TypeError) as err:
        code = str(err)
        report["reason"] = code if code in REASONS else "HOST_PREFLIGHT_OR_RUNTIME_HOLD"
    return report


def _attest_rootless_release(current, release, release_raw):
    """Exact immutable RC6 PAPER release, not a provider identity authority."""
    if (not isinstance(current, dict) or not isinstance(release, dict) or
            not isinstance(release_raw, bytes) or len(release_raw) > 2 * 1024**2):
        raise ValueError("ROOTLESS_RELEASE_CUSTODY_HOLD")
    pinned = {"source_sha": ROOTLESS_CANDIDATE_SHA,
              "source_tree": ROOTLESS_CANDIDATE_TREE,
              "image_id": ROOTLESS_IMAGE_ID}
    if (any(current.get(k) != v or release.get(k) != v for k, v in pinned.items()) or
            current.get("schema") != "porota.rootless.deploy.v1" or
            current.get("state") != "VALIDADO_RUNTIME" or
            current.get("phase") != "COMPLETE" or
            current.get("operating_mode") != "PRODUCTION_PAPER" or
            current.get("execution_mode") != "SIMULATION" or
            type(current.get("real_orders_sent")) is not int or
            current["real_orders_sent"] != 0 or
            current.get("release_manifest_sha256") != sha256(release_raw).hexdigest() or
            release.get("schema") != "porota.release.v1" or
            release.get("operating_mode") != "PRODUCTION_PAPER" or
            release.get("execution_mode") != "SIMULATION" or
            release.get("real_order_capability") != "BLOCKED" or
            type(release.get("real_orders_sent_required")) is not int or
            release["real_orders_sent_required"] != 0):
        raise ValueError("ROOTLESS_RELEASE_CUSTODY_HOLD")
    roles = current.get("roles")
    attempt = current.get("attempt")
    if (not isinstance(roles, dict) or set(roles) != {"observer", "dashboard"} or
            not all(isinstance(cid, str) and CID64.fullmatch(cid) for cid in roles.values()) or
            roles["observer"] == roles["dashboard"] or
            not isinstance(attempt, str) or not ATTEMPT.fullmatch(attempt)):
        raise ValueError("ROOTLESS_RELEASE_CUSTODY_HOLD")
    return roles["observer"]


def _attest_rootless_observer(info, observer, current, *, uid, home):
    """Project only trusted predicates from private Docker JSON; never log Env."""
    expected_store = str(Path(home) / ".local/share/porota-rc6/docker")
    if (not isinstance(info, dict) or not isinstance(observer, dict) or
            type(uid) is not int or uid <= 0 or
            "name=rootless" not in info.get("SecurityOptions", []) or
            str(info.get("CgroupVersion")) != "2" or
            info.get("CgroupDriver") != "systemd" or
            info.get("DockerRootDir") != expected_store):
        raise ValueError("ROOTLESS_DAEMON_HOLD")
    role = (current.get("roles") or {}).get("observer")
    attempt = current.get("attempt")
    config = observer.get("Config") or {}
    labels = config.get("Labels") or {}
    state = observer.get("State") or {}
    host = observer.get("HostConfig") or {}
    if (observer.get("Id") != role or observer.get("Image") != ROOTLESS_IMAGE_ID or
            observer.get("Name") != f"/porota-rc6-observer-{attempt}" or
            labels.get("io.porota.project") != "rc6" or
            labels.get("io.porota.role") != "observer" or
            labels.get("io.porota.attempt") != attempt or
            config.get("Image") != ROOTLESS_IMAGE_ID or
            config.get("User") != "0:0" or config.get("Entrypoint") not in (None, []) or
            config.get("WorkingDir") != "/app" or
            config.get("Cmd") != ["python", "bv_paper_runtime.py"] or
            state.get("Running") is not True or state.get("OOMKilled") is True or
            host.get("ReadonlyRootfs") is not True or host.get("Privileged") is not False or
            host.get("CapDrop") != ["ALL"] or host.get("CapAdd") not in (None, []) or
            host.get("NetworkMode") in ("host", "none") or
            str(host.get("NetworkMode", "")).startswith("container:") or
            host.get("PidMode") == "host" or
            type(host.get("Memory")) is not int or not 0 < host["Memory"] <= 2 * 1024**3 or
            type(host.get("NanoCpus")) is not int or not 0 < host["NanoCpus"] <= 2_000_000_000 or
            type(host.get("PidsLimit")) is not int or not 0 < host["PidsLimit"] <= 512 or
            (host.get("PortBindings") or {}) != {} or
            not any(s in {"no-new-privileges", "no-new-privileges:true"}
                    for s in host.get("SecurityOpt", []))):
        raise ValueError("ROOTLESS_OBSERVER_HOLD")
    raw_env = config.get("Env")
    if not isinstance(raw_env, list) or len(raw_env) > 512:
        raise ValueError("ROOTLESS_OBSERVER_HOLD")
    env = {}
    for row in raw_env:
        if not isinstance(row, str) or len(row) > 4096:
            raise ValueError("ROOTLESS_OBSERVER_HOLD")
        key, sep, value = row.partition("=")
        if not sep or key in env:
            raise ValueError("ROOTLESS_OBSERVER_HOLD")
        if key in {"POROTA_BUILD_SHA", "POROTA_CANDIDATE_TREE_SHA",
                   "PAPER_V17_DB_PATH", "DASHBOARD_OPERATION_MODE",
                   "EXECUTION_MODE", "REAL_ORDER_CAPABILITY"}:
            env[key] = value
    required = {"POROTA_BUILD_SHA": ROOTLESS_CANDIDATE_SHA,
                "POROTA_CANDIDATE_TREE_SHA": ROOTLESS_CANDIDATE_TREE,
                "PAPER_V17_DB_PATH": "/app/data/paper_v17/observer_v17.db",
                "DASHBOARD_OPERATION_MODE": "PRODUCTION_PAPER",
                "EXECUTION_MODE": "SIMULATION", "REAL_ORDER_CAPABILITY": "BLOCKED"}
    if env != required:
        raise ValueError("ROOTLESS_OBSERVER_HOLD")
    expected = {"/app/data": ("/opt/porota-trading/data", True),
                "/app/operator": (str(Path(home) / ".config/porota-rc6/operator"), False),
                "/run/secrets/ppi_production.json": (
                    "/opt/porota-trading/.secrets/ppi_production.json", False)}
    mounts = observer.get("Mounts")
    if not isinstance(mounts, list):
        raise ValueError("ROOTLESS_OBSERVER_HOLD")
    binds = [m for m in mounts if isinstance(m, dict) and m.get("Type") == "bind"]
    tmpfs_sizes = {"/tmp": 128 * 1024**2,
                   "/home/botuser/.cache": 64 * 1024**2,
                   "/home/botuser/.config": 16 * 1024**2}
    if (len(binds) != len(expected) or
            {m.get("Destination") for m in binds} != set(expected) or
            not all((m.get("Source"), m.get("RW")) == expected[m["Destination"]]
                    for m in binds) or
            any(not isinstance(m, dict) or not (m.get("Type") == "bind" or
                m.get("Type") == "tmpfs" and m.get("Destination") in tmpfs_sizes)
                for m in mounts)):
        raise ValueError("ROOTLESS_OBSERVER_HOLD")
    tmpfs = host.get("Tmpfs") or {}
    if not isinstance(tmpfs, dict) or set(tmpfs) != set(tmpfs_sizes):
        raise ValueError("ROOTLESS_OBSERVER_HOLD")
    for destination, options in tmpfs.items():
        flags = set(str(options).split(","))
        sizes = [re.fullmatch(r"size=(\d+)([kKmMgG]?)(?:[bB])?", part)
                 for part in flags if part.startswith("size=")]
        if ({"rw", "noexec", "nosuid"} - flags or {"exec", "suid"} & flags or
                len(sizes) != 1 or sizes[0] is None):
            raise ValueError("ROOTLESS_OBSERVER_HOLD")
        amount = int(sizes[0].group(1)) * {
            "": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[sizes[0].group(2).lower()]
        if amount != tmpfs_sizes[destination]:
            raise ValueError("ROOTLESS_OBSERVER_HOLD")
    return True


def _private_rootless_directory(path, uid, *, allow_private_parent_group_write=False):
    try:
        meta = path.lstat()
        if (not stat.S_ISDIR(meta.st_mode) or meta.st_uid != uid or
                meta.st_mode & (0o002 if allow_private_parent_group_write else 0o022)):
            raise ValueError("ROOTLESS_PRIVATE_PATH_HOLD")
    except (OSError, ValueError):
        raise ValueError("ROOTLESS_PRIVATE_PATH_HOLD") from None
    return meta


def _private_rootless_read(path, uid, size_limit, *, parent_fd=None):
    """FD-pinned, no-follow regular private source; detect replaced/stale paths."""
    handle = None
    try:
        handle = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                         dir_fd=parent_fd)
        before = os.fstat(handle)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != uid or
                before.st_nlink != 1 or before.st_mode & 0o077 or
                not 0 < before.st_size <= size_limit):
            raise ValueError("ROOTLESS_PRIVATE_PATH_HOLD")
        raw = os.read(handle, size_limit + 1)
        after = os.fstat(handle)
        path_now = os.stat(path, follow_symlinks=False, dir_fd=parent_fd)
        fields = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid,
                            s.st_nlink, s.st_size, s.st_mtime_ns)
        if (len(raw) != before.st_size or fields(before) != fields(after) or
                fields(before) != fields(path_now)):
            raise ValueError("ROOTLESS_STATE_DRIFT_HOLD")
        return raw
    except (OSError, ValueError) as err:
        if str(err) == "ROOTLESS_STATE_DRIFT_HOLD":
            raise
        raise ValueError("ROOTLESS_PRIVATE_PATH_HOLD") from None
    finally:
        if handle is not None:
            os.close(handle)


def _rootless_state(home, uid):
    # The dedicated state is UID-private. Historic ~/.local/state may be g+w
    # only under an independently verified private ~/.local parent.
    home = Path(home)
    if not home.is_absolute():
        raise ValueError("ROOTLESS_PRIVATE_PATH_HOLD")
    # Open the private path component by component with pinned directory FDs.
    # A moved or substituted symlink cannot redirect current/release readers.
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    with ExitStack() as stack:
        try:
            fd = os.open(home, flags)
            stack.callback(os.close, fd)
            meta = os.fstat(fd)
            if (not stat.S_ISDIR(meta.st_mode) or meta.st_uid != uid or
                    meta.st_mode & 0o022):
                raise ValueError("ROOTLESS_PRIVATE_PATH_HOLD")
            private_local = False
            for component in (".local", "state", "porota-rc6"):
                next_fd = os.open(component, flags, dir_fd=fd)
                stack.callback(os.close, next_fd)
                fd = next_fd
                meta = os.fstat(fd)
                if not stat.S_ISDIR(meta.st_mode) or meta.st_uid != uid:
                    raise ValueError("ROOTLESS_PRIVATE_PATH_HOLD")
                if component == ".local":
                    private_local = not bool(meta.st_mode & 0o077)
                if meta.st_mode & (0o002 if component == "state" and private_local else 0o022):
                    raise ValueError("ROOTLESS_PRIVATE_PATH_HOLD")
                if component == "porota-rc6" and meta.st_mode & 0o077:
                    raise ValueError("ROOTLESS_PRIVATE_PATH_HOLD")
            current_raw = _private_rootless_read("current.json", uid, 128 * 1024,
                                                 parent_fd=fd)
            try:
                current = json.loads(current_raw)
            except (ValueError, UnicodeError):
                raise ValueError("ROOTLESS_RELEASE_CUSTODY_HOLD") from None
            if not isinstance(current, dict) or current.get("source_sha") != ROOTLESS_CANDIDATE_SHA:
                raise ValueError("ROOTLESS_RELEASE_CUSTODY_HOLD")
            for component in ("releases", ROOTLESS_CANDIDATE_SHA):
                next_fd = os.open(component, flags, dir_fd=fd)
                stack.callback(os.close, next_fd)
                fd = next_fd
                meta = os.fstat(fd)
                if (not stat.S_ISDIR(meta.st_mode) or meta.st_uid != uid or
                        meta.st_mode & 0o022):
                    raise ValueError("ROOTLESS_PRIVATE_PATH_HOLD")
            release_raw = _private_rootless_read("release.json", uid, 2 * 1024**2,
                                                 parent_fd=fd)
        except OSError:
            raise ValueError("ROOTLESS_PRIVATE_PATH_HOLD") from None
    try:
        release = json.loads(release_raw)
    except (ValueError, UnicodeError):
        raise ValueError("ROOTLESS_RELEASE_CUSTODY_HOLD") from None
    cid = _attest_rootless_release(current, release, release_raw)
    token = sha256(current_raw + b"\0" + release_raw).hexdigest()
    return current, cid, token


def _rootless_docker_json(command, environment, *, run=subprocess.run):
    try:
        finished = run(command, check=False, stdout=subprocess.PIPE,
                       stderr=subprocess.DEVNULL, timeout=8, text=True,
                       env=environment, cwd="/")
        if (finished.returncode != 0 or not isinstance(finished.stdout, str) or
                not 0 < len(finished.stdout) <= 128 * 1024):
            raise ValueError("ROOTLESS_DOCKER_TRANSPORT_HOLD")
        return json.loads(finished.stdout)
    except (OSError, subprocess.SubprocessError, ValueError, UnicodeError):
        raise ValueError("ROOTLESS_DOCKER_TRANSPORT_HOLD") from None


def _rootless_context(*, run=subprocess.run, runtime_override=None):
    """One exact private, running and immutable RC6 observer; zero provider GET."""
    uid = os.getuid()
    if uid != ROOTLESS_UID or os.geteuid() != uid or os.getgid() != os.getegid():
        raise ValueError("ROOTLESS_SSH_IDENTITY_HOLD")
    try:
        account = pwd.getpwuid(uid)
    except KeyError:
        raise ValueError("ROOTLESS_SSH_IDENTITY_HOLD") from None
    home = Path(account.pw_dir)
    if not home.is_absolute() or account.pw_name.startswith("boca"):
        raise ValueError("ROOTLESS_SSH_IDENTITY_HOLD")
    runtime = (Path(runtime_override) if runtime_override is not None
               else Path(f"/run/user/{uid}"))
    _private_rootless_directory(runtime, uid)
    socket = runtime / "porota-rc6-docker.sock"
    try:
        socket_stat = socket.lstat()
        if (not stat.S_ISSOCK(socket_stat.st_mode) or socket_stat.st_uid != uid or
                socket_stat.st_mode & 0o002):
            raise ValueError("ROOTLESS_SOCKET_HOLD")
    except (OSError, ValueError):
        raise ValueError("ROOTLESS_SOCKET_HOLD") from None
    current, cid, state_token = _rootless_state(home, uid)
    # Match the reviewed rootless release helper's environment/explicit socket.
    environment = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C",
                   "HOME": str(home), "USER": account.pw_name,
                   "LOGNAME": account.pw_name,
                   "XDG_RUNTIME_DIR": str(runtime),
                   "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/bus",
                   "DOCKER_CONFIG": str(home / ".config/porota-rc6/client"),
                   "DOCKER_CLI_HINTS": "false", "COMPOSE_DISABLE_ENV_FILE": "1"}
    docker = ("/usr/bin/docker", "--host", f"unix://{socket}")
    info = _rootless_docker_json((*docker, "info", "--format", "{{json .}}"),
                                 environment, run=run)
    item = _rootless_docker_json((*docker, "container", "inspect", cid),
                                 environment, run=run)
    if not isinstance(item, list) or len(item) != 1:
        raise ValueError("ROOTLESS_OBSERVER_HOLD")
    _attest_rootless_observer(info, item[0], current, uid=uid, home=home)
    if not isinstance(info.get("ID"), str) or info["ID"] != current.get("daemon_id"):
        raise ValueError("ROOTLESS_DAEMON_HOLD")
    # Never include Docker Config.Env, credentials, raw JSON or host paths in
    # the public receipt. Only internal command/identity values are returned.
    return {"cid": cid, "state_token": state_token, "docker": docker,
            "environment": environment}


def rootless_host_capture(target_b64, candidate, tree, image, source_bytes,
                          *, local_pilot=False, pilot_slot="", pilot_date="",
                          postclose=False, run=subprocess.run):
    """In-memory code stream through rootless Docker; budget SQLite may write."""
    result = {"schema": ROOTLESS_SCHEMA, "state": "NO_VERIFICADO",
              "reason": "ROOTLESS_PIN_MISMATCH", "runtime_pin_verified": False,
              "host_file_writes": 0, "provider_payload_uploaded": False,
              "paper_admission_certified": False, "source_pit_verified": False,
              "receipt": unverified_transport_receipt("ROOTLESS_PIN_MISMATCH")}
    if (candidate != ROOTLESS_CANDIDATE_SHA or tree != ROOTLESS_CANDIDATE_TREE or
            image != ROOTLESS_IMAGE_ID or not isinstance(source_bytes, bytes) or
            not 0 < len(source_bytes) <= 1_000_000):
        return result
    try:
        target = decode_target(target_b64)
        if postclose and not local_pilot:
            result["reason"] = "PPI_LOCAL_PILOT_SLOT_OR_SESSION_HOLD"
            return result
        if local_pilot and (pilot_slot not in SLOTS or
                target_for_slot(pilot_slot) != target or
                not re.fullmatch(r"\d{4}-\d{2}-\d{2}", pilot_date)):
            result["reason"] = "PPI_LOCAL_PILOT_SLOT_OR_SESSION_HOLD"
            return result
        before = _rootless_context(run=run)
        pilot_env = (("-e", "RC6_A630_LOCAL_PILOT=ONCE",
                      "-e", f"RC6_A630_LOCAL_SLOT={pilot_slot}",
                      "-e", f"RC6_A630_LOCAL_ART_DATE={pilot_date}",
                      *(("-e", "RC6_A630_POSTCLOSE=ONCE") if postclose else ()))
                     if local_pilot is True else ())
        command = (*before["docker"], "exec", "-i",
                   "-e", f"RC6_A630_TARGET_B64={target_b64}",
                   "-e", f"RC6_A630_EXPECTED_PRODUCT_SHA={PRODUCT_SHA}",
                   "-e", "PYTHONDONTWRITEBYTECODE=1", *pilot_env, before["cid"],
                   "python", "-B", "-", "container-capture")
        try:
            captured = run(command, input=source_bytes, check=False,
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=55, env=before["environment"], cwd="/")
        except (OSError, subprocess.SubprocessError):
            raise ValueError("ROOTLESS_DOCKER_TRANSPORT_HOLD") from None
        if (captured.returncode not in (0, 2) or not isinstance(captured.stdout, bytes) or
                not 0 < len(captured.stdout) <= 65536):
            raise ValueError("ROOTLESS_EXEC_RECEIPT_HOLD")
        try:
            payload = json.loads(captured.stdout)
        except (ValueError, UnicodeError, TypeError):
            raise ValueError("ROOTLESS_EXEC_RECEIPT_HOLD") from None
        if (not verify_receipt(payload, target) or
                (captured.returncode == 0 and payload.get("state") != "EVIDENCE_ONLY_NO_GO") or
                (captured.returncode == 2 and payload.get("state") != "NO_VERIFICADO")):
            raise ValueError("ROOTLESS_EXEC_RECEIPT_HOLD")
        after = _rootless_context(run=run)
        if before["state_token"] != after["state_token"] or before["cid"] != after["cid"]:
            raise ValueError("ROOTLESS_STATE_DRIFT_HOLD")
        result.update(state=payload["state"], reason=payload["reason"],
                      runtime_pin_verified=True, receipt=payload)
    except (OSError, ValueError, TypeError, KeyError) as err:
        code = str(err)
        result.update(reason=code if code in REASONS else "ROOTLESS_DOCKER_TRANSPORT_HOLD",
                      receipt=unverified_transport_receipt(
                          code if code in REASONS else "ROOTLESS_DOCKER_TRANSPORT_HOLD"))
    return result


def verified_rootless_wrapper(raw, target):
    """Sanitize host stdout by a CLOSED schema and the existing A630 validator.

    Typed provider/budget HOLDs remain evidence. Uncertain SSH/exec transport
    has unknown provider GET counts, never a fabricated zero. No raw Docker
    inspect or credential-bearing arbitrary keys can reach the public artifact.
    """
    keys = {"schema", "state", "reason", "runtime_pin_verified",
            "host_file_writes", "provider_payload_uploaded",
            "paper_admission_certified", "source_pit_verified", "receipt"}
    if not isinstance(raw, bytes) or not 0 < len(raw) <= 65536:
        return None
    try:
        doc = json.loads(raw)
        if (not isinstance(doc, dict) or set(doc) != keys or
                doc["schema"] != ROOTLESS_SCHEMA or
                doc["state"] not in {"NO_VERIFICADO", "EVIDENCE_ONLY_NO_GO"} or
                doc["reason"] not in REASONS or
                type(doc["runtime_pin_verified"]) is not bool or
                doc["host_file_writes"] != 0 or
                doc["provider_payload_uploaded"] is not False or
                doc["paper_admission_certified"] is not False or
                doc["source_pit_verified"] is not False):
            return None
        inner = doc["receipt"]
        full = verify_receipt(inner, target)
        unknown = inner == unverified_transport_receipt(doc["reason"])
        if (not (full or unknown) or inner.get("state") != doc["state"] or
                inner.get("reason") != doc["reason"]):
            return None
        if (doc["state"] == "EVIDENCE_ONLY_NO_GO" and
                (not full or doc["runtime_pin_verified"] is not True)):
            return None
        if doc["state"] == "NO_VERIFICADO" and full and inner.get("state") != "NO_VERIFICADO":
            return None
        return doc
    except (TypeError, ValueError, KeyError, AttributeError):
        return None


def _utc(raw):
    if not isinstance(raw, str) or not 1 <= len(raw) <= 64:
        raise ValueError("PPI_NATIVE_CLOCK_INVALID")
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("PPI_NATIVE_CLOCK_INVALID") from None
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("PPI_NATIVE_CLOCK_INVALID")
    return dt.astimezone(timezone.utc)


def _observed_utc(clock):
    """Measure *after* an SDK GET; a naive or unavailable clock fails closed."""
    result = clock()
    if (not isinstance(result, datetime) or result.tzinfo is None or
            result.utcoffset() is None):
        raise ValueError("PPI_NATIVE_CLOCK_INVALID")
    return result.astimezone(timezone.utc)


def _exit_round_fresh(cycle, *, now=None):
    if not isinstance(cycle, dict):
        return False
    try:
        # runtime_budget_snapshot() exposes normalized UNIX epoch seconds,
        # not an ISO string. It has already structurally checked its own
        # current ledger coverage and basis when returning OBSERVED.
        at = cycle.get("recorded_at")
        if type(at) not in (int, float) or not math.isfinite(at) or at < 0:
            return False
        current = now or datetime.now(timezone.utc)
        if not isinstance(current, datetime) or current.tzinfo is None:
            return False
        age_ms = (current.astimezone(timezone.utc).timestamp() - at) * 1000
        return 0 <= age_ms <= MAX_EXIT_ROUND_AGE_MS
    except (TypeError, ValueError):
        return False


def _ppi_url_path(method, url, target):
    """Independently pre-check the PreparedRequest before HTTPAdapter.send."""
    try:
        parsed = urlsplit(str(url))
        if (parsed.scheme != "https" or parsed.hostname != PPI_HOST
                or parsed.port not in (None, 443) or parsed.username is not None
                or parsed.password is not None or bool(parsed.fragment)):
            raise ValueError("PROVIDER_GET_SCOPE_OR_COUNT_INVALID")
        path = parsed.path.lower().rstrip("/")
        if method == "POST" and path in {
                "/api/1.0/account/loginapi", "/api/1.0/account/refreshtoken"}:
            if parsed.query:
                raise ValueError("PROVIDER_GET_SCOPE_OR_COUNT_INVALID")
            return path
        if method != "GET" or path not in {
                "/api/1.0/marketdata/current", "/api/1.0/marketdata/book"}:
            raise ValueError("PROVIDER_GET_SCOPE_OR_COUNT_INVALID")
        query = [(k.lower(), value) for k, value in
                 parse_qsl(parsed.query, keep_blank_values=True)]
        if (len(query) != 3 or len({key for key, _ in query}) != 3 or
                dict(query) != {"ticker": target[0],
                                "type": PPI_TYPES[target[1]],
                                "settlement": target[4]}):
            raise ValueError("PROVIDER_GET_SCOPE_OR_COUNT_INVALID")
        return path
    except (ValueError, TypeError):
        raise ValueError("PROVIDER_GET_SCOPE_OR_COUNT_INVALID") from None


class _Silence:
    def write(self, content):
        return len(content)

    def flush(self):
        pass

    def isatty(self):
        return False


def existing_container_keypair():
    path = os.environ.get("PPI_PRODUCTION_SECRET_FILE", "/run/secrets/ppi_production.json")
    if not isinstance(path, str) or not path.startswith("/") or len(path) > 512:
        return None
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or not 0 < st.st_size <= 4096:
            return None
        with os.fdopen(fd, "rb") as source:
            fd = None
            pairs = source.read(4097)
        def unique(items):
            found = {}
            for name, value in items:
                if name in found:
                    raise ValueError("DUPLICATE_SECRET_FIELD")
                found[name] = value
            return found
        data = json.loads(pairs, object_pairs_hook=unique)
        if isinstance(data, dict) and all(isinstance(data.get(key), str) and
                1 <= len(data[key]) <= 2048 for key in ("api_key", "api_secret")):
            return data["api_key"], data["api_secret"]
    except (OSError, ValueError, UnicodeError, TypeError):
        pass
    finally:
        if fd is not None:
            os.close(fd)
    return None


class LocalPilotBudget:
    """Separate diagnostic debt, exclusive canonical bootstrap-lock custody.

    Only while canonical budget is ABSENT. The runtime bootstrap itself takes
    this same OS flock before creating global.sqlite, so production can never
    inherit local policy/debt. Release even on failed HTTP; killed process
    releases its open descriptor. The harmless bootstrap lock file may remain.
    """
    def __init__(self, inner, database, operational_sidecar, bootstrap_fd):
        self.inner, self.database = inner, database
        self.operational_sidecar = operational_sidecar
        self.bootstrap_fd = bootstrap_fd

    def close(self):
        if self.bootstrap_fd is not None:
            fd, self.bootstrap_fd = self.bootstrap_fd, None
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _check(self):
        from rc6_ppi_global_budget import supervisable_position_count
        if not _local_pilot_admission_paused():
            raise ValueError("PPI_LOCAL_PILOT_PAPER_ADMISSION_HOLD")
        if supervisable_position_count(self.database) != 0:
            raise ValueError("PPI_LOCAL_PILOT_PAPER_LEDGER_HOLD")
        if (self.bootstrap_fd is None or self.operational_sidecar.exists() or
                self.operational_sidecar.is_symlink()):
            raise ValueError("PPI_LOCAL_PILOT_RUNTIME_BUDGET_HOLD")
        if not _local_pilot_session_now():
            raise ValueError("PPI_LOCAL_PILOT_SLOT_OR_SESSION_HOLD")

    def acquire(self, endpoint, *, consumer="UNSCOPED", priority="DISCOVERY"):
        try:
            self._check()
        except ValueError as error:
            return {"allowed": False, "reason": str(error)}
        return self.inner.acquire(endpoint, consumer=consumer, priority=priority)

    def start(self, lease):
        self._check()
        return self.inner.start(lease)

    @contextmanager
    def wire_scope(self, lease):
        self._check()
        with self.inner.wire_scope(lease):
            self._check()
            yield

    def finish(self, lease, **outcome):
        return self.inner.finish(lease, **outcome)

    def report_error(self, endpoint, code):
        return self.inner.report_error(endpoint, code)


def _local_pilot_session_now(*, now=None):
    """Recheck ART session at the actual HTTP seam, not only in Actions.

    A670 already established the official BYMA day/calendar identity. Local
    secondary check binds the same ART date+slot to this Docker child and
    stops at 16:45 ART (well before end of spot session). Numeric limits are
    local diagnostic limits, not a new provider policy.
    """
    slot = os.environ.get("RC6_A630_LOCAL_SLOT", "")
    target_day = os.environ.get("RC6_A630_LOCAL_ART_DATE", "")
    if slot not in SLOTS or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", target_day):
        return False
    stamp = now or datetime.now(timezone.utc)
    if not isinstance(stamp, datetime) or stamp.tzinfo is None:
        return False
    art = stamp.astimezone(ZoneInfo("America/Argentina/Buenos_Aires"))
    minutes = art.hour * 60 + art.minute
    first = int(slot[:2]) * 60 + int(slot[2:])
    if not (art.weekday() < 5 and art.date().isoformat() == target_day):
        return False
    if os.environ.get("RC6_A630_POSTCLOSE") == "ONCE":
        # The audited calendar/time gate ran in Actions before remote access;
        # recheck the same ART date and CLOSED window at each budget/wire seam.
        return 17 * 60 <= minutes < 23 * 60
    return first <= minutes <= min(first + 59, 16 * 60 + 45)


def _local_pilot_admission_paused():
    """Require authenticated owner PAUSE, not merely a failing entry check.

    `admission_status()` alone fails closed for several unrelated reasons,
    including an unreadable receipt. Those failures are NOT proof that PAPER
    entry cannot rearm while this diagnostic budget holds a bootstrap lock.
    """
    try:
        from scripts.rc6_operator_paper_rearm import PAUSE_STATUS, trusted_operator_pause
        from rc6_paper_admission_gate import admission_status
        now = datetime.now(timezone.utc)
        paused = trusted_operator_pause(now=now, context="runtime")
        if (not isinstance(paused, dict)
                or paused.get("status") != PAUSE_STATUS):
            return False
        allowed, reason = admission_status(now=now)
        return (allowed is False
                and reason == "OPERATOR_NO_GO_PENDING_EXPLICIT_REARM")
    except (OSError, ValueError, TypeError, KeyError, ImportError):
        return False


def _sanitized_native_auth_reason(error):
    """Only typed known ProductAuthResponseError metadata; no SDK text leak."""
    if type(error).__name__ != "ProductionAuthResponseError":
        return None
    stamp = re.fullmatch(
        r"PPI_PRODUCTION_AUTH_(?:PROVIDER_ERROR|NON_JSON_RESPONSE): "
        r"HTTP=(401|403|429); Content-Type=(?:APPLICATION_JSON|"
        r"APPLICATION_PROBLEM_JSON|TEXT_HTML|TEXT_PLAIN|OTHER|ABSENT|UNKNOWN); "
        r"Content-Length=(?:UNKNOWN|[0-9]{1,8})", str(error))
    return "PPI_HTTP_" + stamp.group(1) if stamp else None


def _local_once_budget(database, sidecar):
    """Cold-start two-GET lease, NOT a PPI quota claim or a PAPER readiness.

    One-hour rolling debt exceeds the 90-second local authority. The private
    sidecar persists across Action retries. Runtime global.sqlite is NEVER
    opened or written, and its bootstrap lock excludes concurrent activation.
    """
    fd = None
    try:
        from rc6_ppi_global_budget import GlobalPPIBudget, SCHEMA as BUDGET_SCHEMA
        from rc6_ppi_global_budget import supervisable_position_count
        if not _local_pilot_session_now():
            return None, "PPI_LOCAL_PILOT_SLOT_OR_SESSION_HOLD"
        if not _local_pilot_admission_paused():
            return None, "PPI_LOCAL_PILOT_PAPER_ADMISSION_HOLD"
        if supervisable_position_count(database) != 0:
            return None, "PPI_LOCAL_PILOT_PAPER_LEDGER_HOLD"
        if sidecar.exists() or sidecar.is_symlink():
            return None, "PPI_LOCAL_PILOT_RUNTIME_BUDGET_HOLD"
        # This exact path is the pre-existing native bootstrap mutex; unlike
        # orphan *.wire.lock it does not invalidate runtime_budget_snapshot.
        if any(parent.is_symlink() for parent in (sidecar, *sidecar.parents)):
            return None, "PPI_BUDGET_SIDECAR_PATH_UNSAFE"
        sidecar.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        mutex = Path(str(sidecar) + ".bootstrap.lock")
        if mutex.is_symlink():
            return None, "PPI_BUDGET_SIDECAR_PATH_UNSAFE"
        fd = os.open(mutex, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        current = os.fstat(fd)
        located = mutex.stat(follow_symlinks=False)
        if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1 or
                (current.st_dev, current.st_ino) != (located.st_dev, located.st_ino)):
            raise ValueError("PPI_LOCAL_PILOT_STATE_UNAVAILABLE")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (sidecar.exists() or sidecar.is_symlink()
                or supervisable_position_count(database) != 0):
            return None, "PPI_LOCAL_PILOT_RUNTIME_BUDGET_HOLD"
        if not _local_pilot_admission_paused():
            return None, "PPI_LOCAL_PILOT_PAPER_ADMISSION_HOLD"
        private = sidecar.parent.parent / "ppi-a670-diagnostic" / "budget.sqlite"
        # Globally one-shot per artifact, even if no GET was completed. Never
        # mint a new authority after one hour or an unsuccessful login.
        if private.exists() or private.is_symlink():
            return None, "PPI_LOCAL_PILOT_ALREADY_USED"
        now = datetime.now(timezone.utc)
        version = f"rc6-a630-local-once-v1:{PRODUCT_SHA}:{now.isoformat()}"
        token = sha256(version.encode("utf-8")).hexdigest()
        policy = {
            "schema": BUDGET_SCHEMA, "version": "rc6-a630-local-once-v1",
            "authority": "LOCAL_DIAGNOSTIC_ONLY_NOT_PROVIDER_QUOTA",
            "recommendation_digest": token, "configuration_fingerprint": token,
            "window_seconds": 3600,
            "endpoint_limits": {"current": 1, "book": 1, "intraday": 1},
            "global_limit": 2, "max_parallel_requests": 1,
            "safety_reserve": "LOCAL_DIAGNOSTIC_ONLY; PROVIDER_QUOTA_NO_VERIFICADO",
            "priority_reserves": {}, "open_positions_count": 0, "exit_demand": {},
            "expires_at": (now + timedelta(seconds=90)).isoformat(),
            "critical_book_seconds": 5, "lease_seconds": 60,
            "breaker_seconds": 3600, "session_breaker_seconds": 3600,
            "server_error_threshold": 1, "maximum_bytes": 8 * 1024**2,
        }
        shared = GlobalPPIBudget(private, policy, protected=[database, sidecar])
        budget = LocalPilotBudget(shared, database, sidecar, fd)
        fd = None  # ownership transferred until container capture finally
        return budget, None
    except (OSError, ValueError, TypeError, RuntimeError, ImportError, BlockingIOError):
        return None, "PPI_LOCAL_PILOT_STATE_UNAVAILABLE"
    finally:
        if fd is not None:
            os.close(fd)


def _existing_shared_budget():
    from cg_paper_workspace import artifact_root, database_path
    from rc6_ppi_global_budget import (RuntimePPIBudget, budget_from_environment,
                                       runtime_budget_snapshot)
    database = database_path()
    sidecar = artifact_root(database) / "ppi-budget/global.sqlite"
    if any(path.is_symlink() for path in (sidecar, *sidecar.parents)):
        return None, "PPI_BUDGET_SIDECAR_PATH_UNSAFE"
    if os.environ.get("RC6_A630_LOCAL_PILOT") == "ONCE":
        return _local_once_budget(database, sidecar)
    try:
        before = sidecar.stat()
        if not sidecar.is_file() or before.st_nlink != 1:
            return None, "PPI_BUDGET_SIDECAR_PATH_UNSAFE"
        snapshot = runtime_budget_snapshot(database)
        if snapshot.get("status") != "OBSERVED":
            return None, "PPI_BUDGET_SNAPSHOT_NOT_OBSERVED"
        if (snapshot.get("receipt_retention") or {}).get("status") != "READY":
            return None, "PPI_RETENTION_NOT_READY"
        exit_service = snapshot.get("exit_service") or {}
        if exit_service.get("lower_suspended") is not False:
            return None, "PPI_EXIT_LOWER_SUSPENDED"
        cycle = exit_service.get("round") or {}
        if (cycle.get("status") != "COMPLETE" or
                cycle.get("required_scope_verified_current") is not True or
                cycle.get("required_scope_basis") != "CURRENT_VERIFIED_PAPER_LEDGER"):
            return None, "PPI_EXIT_ROUND_NOT_ATTESTED"
        if not _exit_round_fresh(cycle):
            return None, "PPI_EXIT_ROUND_STALE_OR_CLOCK_INVALID"
        shared = budget_from_environment(database)
        after = sidecar.stat()
        if (type(shared) is not RuntimePPIBudget or Path(shared.path) != sidecar
                or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)):
            return None, "PPI_BUDGET_RUNTIME_BACKEND_MISMATCH"
        return shared, None
    except FileNotFoundError:
        return None, "PPI_BUDGET_SIDECAR_ABSENT"
    except (OSError, ValueError, TypeError, AttributeError):
        return None, "PPI_BUDGET_PROBE_UNAVAILABLE"


class TwoGetAtomicBudget:
    """Exactly one lease for Current then one for Book, no retries/fallback."""
    def __init__(self, shared):
        self.shared = shared
        self.acquired = []
        self.started = set()
        self.finished = set()

    def acquire(self, endpoint, *, consumer="UNSCOPED", priority="DISCOVERY"):
        expected = ("current", "book")
        if len(self.acquired) >= 2 or endpoint != expected[len(self.acquired)]:
            return {"allowed": False, "reason": "A630_ENDPOINT_ORDER_DENIED"}
        if consumer != "UNSCOPED" or priority != "DISCOVERY":
            return {"allowed": False, "reason": "A630_CONSUMER_PRIORITY_DENIED"}
        # Append BEFORE contacting the budget: no second lease attempt on errors.
        self.acquired.append((endpoint, None))
        admitted = self.shared.acquire(endpoint, consumer=consumer, priority=priority)
        lease = admitted.get("lease") if isinstance(admitted, dict) else None
        if not isinstance(admitted, dict) or admitted.get("allowed") is not True or not (
                isinstance(lease, str) and 1 <= len(lease) <= 256):
            return {"allowed": False, "reason": "ATOMIC_LEASE_DENIED"}
        if any(lease == old for _, old in self.acquired[:-1]):
            return {"allowed": False, "reason": "ATOMIC_LEASE_DENIED"}
        self.acquired[-1] = (endpoint, lease)
        return admitted

    def _check(self, lease):
        if lease not in [value for _, value in self.acquired if value]:
            raise ValueError("ATOMIC_LEASE_DENIED")

    def start(self, lease):
        self._check(lease)
        if lease in self.started:
            raise ValueError("ATOMIC_LEASE_DENIED")
        self.shared.start(lease)
        self.started.add(lease)

    def wire_scope(self, lease):
        """Return a DEFERRED context, matching pinned product guard ordering.

        Product guarded_send obtains ownership = wire_scope(lease), ENTERs it,
        and only then invokes start(lease) BEFORE delegating to the adapter.
        An eager started-check here deadlocks every Current GET. The strict
        HTTPAdapter pre-wire guard checks started immediately before socket.
        """
        self._check(lease)
        if lease in self.finished:
            raise ValueError("ATOMIC_LEASE_DENIED")
        @contextmanager
        def entered():
            if lease in self.finished:
                raise ValueError("ATOMIC_LEASE_DENIED")
            with self.shared.wire_scope(lease):
                yield
        return entered()

    def finish(self, lease, **outcome):
        self._check(lease)
        if lease not in self.started or lease in self.finished:
            raise ValueError("ATOMIC_LEASE_DENIED")
        self.shared.finish(lease, **outcome)
        self.finished.add(lease)

    def report_error(self, endpoint, code):
        if hasattr(self.shared, "report_error"):
            self.shared.report_error(endpoint, code)

    def completed(self):
        return (len(self.acquired) == 2 and all(value for _, value in self.acquired)
                and len(self.started) == len(self.finished) == 2)


@contextmanager
def _strict_adapter_send(target, budget):
    """Deny Search/Estimate/Book2/redirect BEFORE any adapter wire send.

    ProductMarketReader's own SDK guard may use configuration/search GETs in
    other workflows; no such route has authority in this two-slot diagnostic.
    The native shared lease is still mandatory for each of the two sends.
    """
    from requests.adapters import HTTPAdapter
    original = HTTPAdapter.send
    sends = []
    posts = []

    def guarded(adapter, request, *args, **kwargs):
        method = getattr(request, "method", None)
        if method not in {"POST", "GET"} or kwargs.get("verify", True) in (False, None):
            raise ValueError("PROVIDER_GET_SCOPE_OR_COUNT_INVALID")
        path = _ppi_url_path(method, getattr(request, "url", None), target)
        if method == "POST":
            if path in posts or len(posts) >= 2:
                raise ValueError("PROVIDER_GET_SCOPE_OR_COUNT_INVALID")
            posts.append(path)
        else:
            expected = ("/api/1.0/marketdata/current", "/api/1.0/marketdata/book")
            if len(sends) >= 2 or path != expected[len(sends)]:
                raise ValueError("PROVIDER_GET_SCOPE_OR_COUNT_INVALID")
            if len(budget.acquired) != len(sends) + 1:
                raise ValueError("ATOMIC_LEASE_DENIED")
            endpoint, lease = budget.acquired[-1]
            if (endpoint != path.rsplit("/", 1)[1] or not lease
                    or lease not in budget.started or lease in budget.finished):
                raise ValueError("ATOMIC_LEASE_DENIED")
            sends.append(path)  # increment before calling adapter; retry denied
        return original(adapter, request, *args, **kwargs)

    HTTPAdapter.send = guarded
    try:
        yield sends
    finally:
        HTTPAdapter.send = original


@contextmanager
def _http_body_hashes(target):
    """Hash bounded decompressed HTTP response body in the ephemeral process.

    Digest is NOT the original compressed TLS wire bytes, nor a PPI signature.
    Only the SDK's allowed path/host can be recorded; duplicates are rejected.
    """
    from requests.models import Response
    original = Response.iter_content
    hits = []

    def capture(response, chunk_size=1, decode_unicode=False):
        parsed = urlsplit(str(response.url))
        path = parsed.path.lower().rstrip("/")
        if path not in {"/api/1.0/marketdata/current", "/api/1.0/marketdata/book"}:
            return original(response, chunk_size=chunk_size, decode_unicode=decode_unicode)
        _ppi_url_path("GET", response.url, target)
        if any(hit[0] == path for hit in hits):
            raise ValueError("PROVIDER_GET_SCOPE_OR_COUNT_INVALID")
        row = [path, sha256(), 0]
        hits.append(row)
        iterator = original(response, chunk_size=chunk_size, decode_unicode=decode_unicode)

        def limited():
            for chunk in iterator:
                raw = chunk.encode("utf-8") if isinstance(chunk, str) else chunk
                row[2] += len(raw)
                if row[2] > MAX_BODY:
                    raise ValueError("BODY_BUDGET_OR_DIGEST_INVALID")
                row[1].update(raw)
                yield chunk
        return limited()

    Response.iter_content = capture
    try:
        yield hits
    finally:
        Response.iter_content = original


def _base_receipt(target):
    return {"schema": SCHEMA, "phase": "CONTAINER_CAPTURE", "state": "NO_VERIFICADO",
        "reason": "NOT_REQUESTED", "target": list(target), "ppi_type": PPI_TYPES[target[1]],
        "source": "PPI_SDK_HTTPS_ALLOWLIST_NOT_PROVIDER_SIGNED",
        "budget_consumer": "UNSCOPED", "budget_priority": "DISCOVERY",
        "expected_product_sha": PRODUCT_SHA, "mode": "PRODUCTION_PAPER",
        "real_order_capability": "BLOCKED", "orders_sent": 0,
        "trading_db_writes": 0, "history_db_writes": 0, "ppi_watch_mutations": 0,
        "global_budget_lease_only": True, "source_pit_verified": False,
        "independent_provider_signature_verified": False, "paper_sim_verified": False,
        "current_gets": 0, "book_gets": 0, "lease_acquires": 0,
        "adapter_current_sends": 0, "adapter_book_sends": 0,
        "lease_starts": 0, "lease_finishes": 0,
        "first_known_at_utc": None, "current_provider_at_utc": None,
        "book_provider_at_utc": None, "current_http_body_sha256": None,
        "book_http_body_sha256": None, "current_body_bytes": 0,
        "book_body_bytes": 0, "book_bid_levels": 0, "book_offer_levels": 0,
        "http_digest_semantics": "SDK_RESPONSE_DECOMPRESSED_BYTES_NOT_TLS_WIRE",
        "credential_location": "EXISTING_PRODUCT_CONTAINER_ONLY",
        "execution_allowed": False, "raw_provider_payload_persisted": False}


def unverified_transport_receipt(reason="HOST_PREFLIGHT_OR_RUNTIME_HOLD"):
    """No false zero counters after SSH timeout/partial Current or lost stdio.

    A transport timeout can happen AFTER a shared-budget Current GET and before
    Book or receipt emission. Absence of a verified receipt is not proof that
    no provider quota was consumed; counters MUST be unknown, not zero.
    """
    if reason not in REASONS:
        reason = "HOST_PREFLIGHT_OR_RUNTIME_HOLD"
    return {"schema": SCHEMA, "state": "NO_VERIFICADO", "reason": reason,
            "current_gets": None, "book_gets": None,
            "transport_count_authority": "NO_VERIFICADO",
            "orders_sent": 0, "execution_allowed": False,
            "source_pit_verified": False}


def capture_two(target, credentials, shared_budget, reader_factory,
                *, clock=None, http_limiter=None, adapter_guard=None,
                budget_reason=None, allow_stale=False):
    receipt = _base_receipt(target)
    if credentials is None:
        receipt["reason"] = "PPI_KEYPAIR_UNAVAILABLE"
        return receipt
    if shared_budget is None:
        receipt["reason"] = budget_reason if budget_reason in REASONS else (
            "SHARED_BUDGET_OR_EXIT_RESERVE_NOT_ATTESTED")
        return receipt
    budget = TwoGetAtomicBudget(shared_budget)
    reads = {"current": None, "book": None}
    events = []
    reader = None
    adapter_sends = []
    reader_close_failed = False
    provider_error_code = None
    limiter = http_limiter or (lambda: _http_body_hashes(target))
    guard = adapter_guard or (lambda: _strict_adapter_send(target, budget))
    observed = clock or (lambda: datetime.now(timezone.utc))
    current_received = book_received = None
    try:
        def audit(method, path, action):
            if len(events) < 12:
                events.append((method, path, action))
            else:
                events.append(("OVERFLOW", "", "BLOCKED"))
        with redirect_stdout(_Silence()), redirect_stderr(_Silence()):
            # CRITICAL TRANSPORT ORDER: ProductMarketReader.__init__ installs
            # its own ReadOnlyTransportGuard.guarded_send, which acquires and
            # STARTS the native budget lease before calling its saved adapter.
            # Install strict send first so native guarded_send wraps it. Then
            # close the reader BEFORE restoring our strict adapter seam.
            with guard() as adapter_sends:
                try:
                    reader = reader_factory(credentials[0], credentials[1], budget=budget,
                        consumer="UNSCOPED", priority="DISCOVERY", audit=audit)
                    reader.login_once()
                    with limiter() as body:
                        reads["current"] = reader.current(target[0], PPI_TYPES[target[1]], target[4])
                        current_received = _observed_utc(observed)
                        reads["book"] = reader.book(target[0], PPI_TYPES[target[1]], target[4])
                        book_received = _observed_utc(observed)
                finally:
                    if reader is not None:
                        # Only the product guard's typed status, never exception
                        # text, provider body, URL or SDK credentials, is public.
                        provider_error_code = getattr(reader, "last_read_error_code", None)
                        try:
                            reader.close()
                        except Exception:
                            reader_close_failed = True
                        reader = None
        # Keep the historical first_known_at_utc meaning (=pair completion).
        # Distinct post-GET observations prevent a later Book timestamp from
        # retroactively making a future/stale Current observation look valid.
        now = _observed_utc(observed)
        if not current_received <= book_received <= now:
            raise ValueError("PPI_NATIVE_CLOCK_INVALID")
        current, book = reads["current"], reads["book"]
        if (not isinstance(current, dict) or not isinstance(book, dict)
                or not isinstance(book.get("bids"), list)
                or not isinstance(book.get("offers"), list)
                or len(book["bids"]) > MAX_LEVELS or len(book["offers"]) > MAX_LEVELS):
            raise ValueError("PROVIDER_RESPONSE_SHAPE_INVALID")
        cp, bp = _utc(current.get("date")), _utc(book.get("date"))
        # Require both freshness at each GET and the historical pair-complete
        # freshness gate; long Book latency must not relax the original policy.
        ages = [(received - provider).total_seconds() * 1000
                for provider, received in ((cp, current_received), (bp, book_received),
                                           (cp, now), (bp, now))]
        if (any(age < 0 for age in ages)
                or (not allow_stale and any(age > MAX_AGE_MS for age in ages))):
            raise ValueError("PPI_NATIVE_CLOCK_STALE_OR_FUTURE")
        expected_paths = ["/api/1.0/marketdata/current", "/api/1.0/marketdata/book"]
        if ([row[0] for row in body] != expected_paths
                or any(not 0 < row[2] <= MAX_BODY for row in body)):
            raise ValueError("BODY_BUDGET_OR_DIGEST_INVALID")
        receipt.update(first_known_at_utc=now.isoformat(),
            current_received_at_utc=current_received.isoformat(),
            book_received_at_utc=book_received.isoformat(),
            pair_completed_at_utc=now.isoformat(),
            current_provider_at_utc=cp.isoformat(), book_provider_at_utc=bp.isoformat(),
            current_http_body_sha256=body[0][1].hexdigest(),
            book_http_body_sha256=body[1][1].hexdigest(),
            current_body_bytes=body[0][2], book_body_bytes=body[1][2],
            book_bid_levels=len(book["bids"]), book_offer_levels=len(book["offers"]))
    except Exception as error:
        typed = _sanitized_native_auth_reason(error)
        if provider_error_code in {"PPI_HTTP_429", "PPI_HTTP_401",
                                   "PPI_HTTP_403", "PPI_SESSION_INVALID"}:
            receipt["reason"] = provider_error_code
        elif typed is not None:
            receipt["reason"] = typed
        else:
            receipt["reason"] = (str(error) if str(error) in REASONS
                                 else "PPI_PROVIDER_OR_AUTH_FAILED")
    finally:
        if reader_close_failed:
            receipt["reason"] = "PPI_PROVIDER_OR_AUTH_FAILED"
        receipt["lease_acquires"] = len(budget.acquired)
        receipt["lease_starts"] = len(budget.started)
        receipt["lease_finishes"] = len(budget.finished)
        receipt["adapter_current_sends"] = adapter_sends.count("/api/1.0/marketdata/current")
        receipt["adapter_book_sends"] = adapter_sends.count("/api/1.0/marketdata/book")
        receipt["current_gets"] = sum(e == ("GET", "/api/1.0/marketdata/current", "ALLOWED")
                                      for e in events)
        receipt["book_gets"] = sum(e == ("GET", "/api/1.0/marketdata/book", "ALLOWED")
                                   for e in events)
        allowed = {("POST", "/api/1.0/account/loginapi", "ALLOWED"),
                   ("POST", "/api/1.0/account/refreshtoken", "ALLOWED"),
                   ("GET", "/api/1.0/marketdata/current", "ALLOWED"),
                   ("GET", "/api/1.0/marketdata/book", "ALLOWED")}
        if (any(event not in allowed for event in events) or
                receipt["current_gets"] > 1 or receipt["book_gets"] > 1 or
                receipt["adapter_current_sends"] > 1 or receipt["adapter_book_sends"] > 1 or
                sum(e == ("POST", "/api/1.0/account/loginapi", "ALLOWED")
                    for e in events) > 1):
            receipt["reason"] = "PROVIDER_GET_SCOPE_OR_COUNT_INVALID"
        if (receipt["reason"] == "NOT_REQUESTED" and budget.completed() and
                receipt["current_gets"] == receipt["book_gets"] == 1 and
                receipt["adapter_current_sends"] == receipt["adapter_book_sends"] == 1 and
                receipt["current_http_body_sha256"] and receipt["book_http_body_sha256"]):
            receipt["state"] = "EVIDENCE_ONLY_NO_GO"
            receipt["reason"] = ("PPI_NATIVE_STALE_POSTCLOSE_DIAGNOSTIC_NO_GO"
                if allow_stale and any(age > MAX_AGE_MS for age in ages)
                else "PPI_GET_RECEIPT_SDK_ONLY_NO_CAUSAL_PIT")
        else:
            for field in PER_GET_CLOCK_FIELDS:
                receipt.pop(field, None)
            receipt.update(first_known_at_utc=None, current_provider_at_utc=None,
                book_provider_at_utc=None, current_http_body_sha256=None,
                book_http_body_sha256=None, current_body_bytes=0,
                book_body_bytes=0, book_bid_levels=0, book_offer_levels=0)
            if receipt["reason"] == "NOT_REQUESTED":
                receipt["reason"] = "ATOMIC_LEASE_DENIED"
    return receipt


def verify_receipt(obj, target):
    if not isinstance(obj, dict):
        return False
    base_keys = set(_base_receipt(target))
    extended = set(obj) == base_keys | PER_GET_CLOCK_FIELDS
    if not (set(obj) == base_keys or extended):
        return False
    fixed = ("schema", "phase", "target", "ppi_type", "source", "expected_product_sha",
        "mode", "real_order_capability", "orders_sent", "trading_db_writes",
        "history_db_writes", "ppi_watch_mutations", "global_budget_lease_only",
        "source_pit_verified", "independent_provider_signature_verified",
        "paper_sim_verified", "http_digest_semantics", "credential_location",
        "budget_consumer", "budget_priority",
        "execution_allowed", "raw_provider_payload_persisted")
    if any(obj[key] != _base_receipt(target)[key] for key in fixed):
        return False
    if obj["reason"] not in REASONS or obj["state"] not in {"NO_VERIFICADO", "EVIDENCE_ONLY_NO_GO"}:
        return False
    for field in ("current_gets", "book_gets", "adapter_current_sends",
                  "adapter_book_sends", "lease_acquires", "lease_starts",
                  "lease_finishes", "current_body_bytes", "book_body_bytes",
                  "book_bid_levels", "book_offer_levels"):
        if type(obj[field]) is not int or not 0 <= obj[field] <= (
                MAX_BODY if "bytes" in field else MAX_LEVELS if "levels" in field else 2):
            return False
    digests = (obj["current_http_body_sha256"], obj["book_http_body_sha256"])
    dates = (obj["first_known_at_utc"], obj["current_provider_at_utc"], obj["book_provider_at_utc"])
    if obj["state"] == "EVIDENCE_ONLY_NO_GO":
        if (obj["reason"] not in {"PPI_GET_RECEIPT_SDK_ONLY_NO_CAUSAL_PIT",
                                   "PPI_NATIVE_STALE_POSTCLOSE_DIAGNOSTIC_NO_GO"} or
                obj["current_gets"] != 1 or obj["book_gets"] != 1 or
                obj["adapter_current_sends"] != 1 or obj["adapter_book_sends"] != 1 or
                any(obj[k] != 2 for k in ("lease_acquires", "lease_starts", "lease_finishes")) or
                not all(isinstance(x, str) and SHA256.fullmatch(x) for x in digests) or
                not all(isinstance(x, str) for x in dates) or
                not all(0 < obj[x] <= MAX_BODY for x in ("current_body_bytes", "book_body_bytes"))):
            return False
        try:
            known, current, book = map(_utc, dates)
            if extended:
                raw_times = [obj[field] for field in (
                    "current_received_at_utc", "book_received_at_utc", "pair_completed_at_utc")]
                received_current, received_book, completed = map(_utc, raw_times)
                # New fields must be canonical UTC instants, not local offsets,
                # and all causal inequalities must hold independently.
                ages = [(received - provider).total_seconds() * 1000
                        for provider, received in ((current, received_current),
                                                   (book, received_book),
                                                   (current, known), (book, known))]
                if (any(t.isoformat() != raw for t, raw in zip(
                        (received_current, received_book, completed), raw_times)) or
                        completed != known or
                        obj["pair_completed_at_utc"] != obj["first_known_at_utc"] or
                        not current <= received_current <= received_book <= completed or
                        not book <= received_book or
                        any(age < 0 for age in ages) or
                        ((any(age > MAX_AGE_MS for age in ages)) !=
                         (obj["reason"] == "PPI_NATIVE_STALE_POSTCLOSE_DIAGNOSTIC_NO_GO"))):
                    return False
                return True
            if obj["reason"] == "PPI_NATIVE_STALE_POSTCLOSE_DIAGNOSTIC_NO_GO":
                return False  # Stale diagnostics require per-GET causal clocks.
            # Archived v1 receipts lack per-GET clocks; remain valid evidence,
            # but must never be promoted to independent causal PIT authority.
            return all(0 <= (known - t).total_seconds() * 1000 <= MAX_AGE_MS for t in (current, book))
        except ValueError:
            return False
    return not extended and all(x is None for x in (*digests, *dates))


def container_main():
    try:
        from _version import MODE, REAL_ORDER_CAPABILITY
        if (MODE != "PRODUCTION_PAPER" or REAL_ORDER_CAPABILITY != "BLOCKED" or
                os.environ.get("RC6_A630_EXPECTED_PRODUCT_SHA") != PRODUCT_SHA):
            raise ValueError("PRODUCT_IMAGE_OR_MODE_HOLD")
        target = decode_target(os.environ.get("RC6_A630_TARGET_B64"))
        credentials = existing_container_keypair()
        with redirect_stdout(_Silence()), redirect_stderr(_Silence()):
            budget, budget_reason = (_existing_shared_budget() if credentials is not None
                                     else (None, "PPI_KEYPAIR_UNAVAILABLE"))
            if credentials and budget:
                from bd_ppi_readonly_guard import ProductionMarketReader
            else:
                ProductionMarketReader = None
            try:
                receipt = capture_two(target, credentials, budget, ProductionMarketReader,
                                      budget_reason=budget_reason,
                                      allow_stale=(os.environ.get("RC6_A630_POSTCLOSE") == "ONCE"
                                         and os.environ.get("RC6_A630_LOCAL_PILOT") == "ONCE"))
            finally:
                if isinstance(budget, LocalPilotBudget):
                    budget.close()
        if not verify_receipt(receipt, target):
            raise ValueError("RECEIPT_INVALID")
        print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
        return 0 if receipt["state"] == "EVIDENCE_ONLY_NO_GO" else 2
    except Exception:
        print(json.dumps({**unverified_transport_receipt("PRODUCT_IMAGE_OR_MODE_HOLD"),
            "phase": "CONTAINER_CAPTURE"}, sort_keys=True))
        return 2


def main():
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if sys.argv[1:] and sys.argv[1] == "host-preflight" and len(sys.argv) == 3:
        result = host_preflight(sys.argv[2])
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0 if result["state"] == "IDENTITY_ONLY_NO_GET" else 2
    if sys.argv[1:] and sys.argv[1] == "rootless-capture" and len(sys.argv) in (6, 9, 10):
        # The runner streams an already blob-pinned copy of THIS program into
        # one remote Python -c bootstrap; it is kept in memory and supplied to
        # docker exec -i without writing an executable anywhere on the host.
        local_pilot = len(sys.argv) >= 9 and sys.argv[6] == "ONCE"
        postclose = len(sys.argv) == 10 and sys.argv[9] == "POSTCLOSE"
        if (len(sys.argv) >= 9 and not local_pilot) or (len(sys.argv) == 10 and not postclose):
            raise SystemExit("A630_LOCAL_PILOT_MODE_INVALID")
        result = rootless_host_capture(*sys.argv[2:6],
            globals().get("__RC6_SOURCE_BYTES__"), local_pilot=local_pilot,
            pilot_slot=sys.argv[7] if local_pilot else "",
            pilot_date=sys.argv[8] if local_pilot else "", postclose=postclose)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0 if result["state"] == "EVIDENCE_ONLY_NO_GO" else 2
    if sys.argv[1:] == ["container-capture"]:
        return container_main()
    raise SystemExit("A630_EXPLICIT_MODE_REQUIRED")


if __name__ == "__main__":
    raise SystemExit(main())
