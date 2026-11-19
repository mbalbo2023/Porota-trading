#!/usr/bin/env python3
"""RC6 OBSERVER one-shot online custody. Never run from an untrusted ref."""

from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import sys
import time
import urllib.request


SCHEMA = "rc6.observer-online-custody-once.v1"
ROOT = Path("/opt/porota-trading/data")
OBSERVER = ROOT / "paper_v17/observer_v17.db"
MARKER = ROOT / "deploy/CURRENT_STATE_V2.json"
CUSTODY = ROOT / "rc6_observer_custody"
EXPECTED_SOURCE_SHA = "b3cbe57a5b4b82016a65a0e253b251abbb84a04c"
EXPECTED_TREE_SHA = "7370fea6c9c6daa802fcdd8ce6fc20c5cf8e31b0"
MAX_DB_BYTES = 5 * 1024**3
MAX_WAL_BYTES = 512 * 1024**2
MIN_RESERVE_BYTES = 2 * 1024**3
RUN_ID = re.compile(r"[1-9][0-9]{7,14}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
OWNER_BRANCH = "mbalbo2023/Porota-trading"
OWNER_ISSUE = f"https://api.github.com/repos/{OWNER_BRANCH}/issues/980"
OWNER_PUBLIC = f"https://github.com/{OWNER_BRANCH}/issues/980#issuecomment-"
BACKUP_TIMEOUT_SECONDS = 600


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


def _release_marker(path: Path) -> dict:
    info = _safe_file(path, maximum=16 * 1024)
    try:
        with path.open("rb") as src:
            raw = src.read(16 * 1024 + 1)
        if len(raw) > 16 * 1024 or path.lstat().st_ino != info.st_ino:
            raise Hold("RELEASE_PAPER_IDENTITY_UNVERIFIED")
        marker = json.loads(raw)
    except (OSError, ValueError, TypeError) as exc:
        raise Hold("RELEASE_PAPER_IDENTITY_UNVERIFIED") from exc
    if (not isinstance(marker, dict)
            or marker.get("candidate_sha") != EXPECTED_SOURCE_SHA
            or marker.get("candidate_tree_sha") != EXPECTED_TREE_SHA
            or marker.get("validation_status") != "VALIDATED_RUNTIME"
            or marker.get("mode") != "PRODUCTION_PAPER"
            or type(marker.get("real_orders_sent")) is not int
            or marker["real_orders_sent"] != 0
            or marker.get("real_order_capability") != "BLOCKED"
            or marker.get("deployment_backend") != "ROOTLESS_SPLIT"):
        raise Hold("RELEASE_PAPER_IDENTITY_UNVERIFIED")
    return marker


def host_preflight(db_path: Path = OBSERVER, marker_path: Path = MARKER,
                   custody_root: Path = CUSTODY) -> dict:
    """Pure metadata read: never open SQLite or create the custody directory."""
    db_path, marker_path, custody_root = map(Path, (db_path, marker_path, custody_root))
    _safe_directory(custody_root.parent)
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
    free = shutil.disk_usage(db_path.parent).free
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
def _custody_lock(custody_root: Path):
    """Only the one-shot handler creates a private host lock."""
    if not custody_root.exists():
        try:
            custody_root.mkdir(mode=0o700)
        except OSError as exc:
            raise Hold("CUSTODY_DIRECTORY_UNSAFE") from exc
    _safe_directory(custody_root)
    info = custody_root.lstat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise Hold("CUSTODY_DIRECTORY_UNSAFE")
    lock_path = custody_root / "custody.lock"
    try:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600)
    except OSError as exc:
        raise Hold("CUSTODY_LOCK_UNSAFE") from exc
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600):
            raise Hold("CUSTODY_LOCK_UNSAFE")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Hold("CUSTODY_OWNER_BUSY") from exc
        yield
    finally:
        os.close(fd)


def _live_paper_zero(db: sqlite3.Connection) -> None:
    try:
        db.execute("PRAGMA query_only=ON")  # Per-connection only; no source DDL.
        row = db.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
    except sqlite3.Error as exc:
        raise Hold("PAPER_ORDERS_UNVERIFIED") from exc
    if (row is None or row[0] != "PRODUCTION_PAPER"
            or type(row[1]) is not int or row[1] != 0):
        raise Hold("PAPER_ORDERS_UNVERIFIED")


def _sha256(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            sha.update(block)
    return sha.hexdigest()


def _verify_restore(path: Path, deadline: float) -> None:
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1",
                                 uri=True, timeout=1)) as restored:
        restored.set_progress_handler(lambda: int(time.monotonic() > deadline), 5000)
        try:
            value = restored.execute("PRAGMA quick_check").fetchone()
        except sqlite3.Error as exc:
            raise Hold("PRIVATE_RESTORE_QUICK_CHECK_FAILED") from exc
        if not value or value[0] != "ok":
            raise Hold("PRIVATE_RESTORE_QUICK_CHECK_FAILED")
        _live_paper_zero(restored)


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
    """Require DB file identity; disclose that live WAL may advance."""
    if (not before.get("db") or not after.get("db")
            or before["db"][:2] != after["db"][:2]):
        raise Hold("SOURCE_IDENTITY_CHANGED")
    wal_state = "UNCHANGED" if before.get("wal") == after.get("wal") else "CHANGED"
    shm_state = "UNCHANGED" if before.get("shm") == after.get("shm") else "CHANGED"
    return f"DB_IDENTITY_STABLE_WAL_{wal_state}_SHM_{shm_state}"


def host_custody_once(db_path: Path = OBSERVER, marker_path: Path = MARKER,
                      custody_root: Path = CUSTODY, *, run_id: str) -> dict:
    """Explicit writer mode: one private online backup + separate restore check.

    Never copy the base file or WAL bytewise. The SQLite backup API merges
    committed WAL transactions into a consistent destination snapshot.
    Partial custody directories are intentionally retained on interruption.
    """
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise Hold("CUSTODY_RUN_ID_INVALID")
    db_path, marker_path, custody_root = map(Path, (db_path, marker_path, custody_root))
    before = host_preflight(db_path, marker_path, custody_root)
    source_before = source_metadata(db_path)
    deadline = time.monotonic() + BACKUP_TIMEOUT_SECONDS
    # A real WAL reader may briefly affect -shm locking; this path requires
    # the separately attested DEPLOY_OWNER and cannot be run by preflight.
    try:
        with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True,
                                     timeout=2)) as source:
            source.execute("PRAGMA busy_timeout=200")
            _live_paper_zero(source)
            final = custody_root / ("observer_" + run_id)
            partial = custody_root / (".partial_" + run_id)
            if final.exists() or final.is_symlink() or partial.exists() or partial.is_symlink():
                raise Hold("CUSTODY_RUN_ALREADY_EXISTS")
            with _private_umask(), _custody_lock(custody_root):
                if final.exists() or partial.exists():
                    raise Hold("CUSTODY_RUN_ALREADY_EXISTS")
                partial.mkdir(mode=0o700)
                snapshot = partial / "snapshot.sqlite"
                restored = partial / "restore-check.sqlite"
                with closing(sqlite3.connect(snapshot, timeout=1)) as target:
                    def progress(status, remaining, total):
                        if time.monotonic() > deadline:
                            raise Hold("CUSTODY_BACKUP_TIMEOUT")
                    source.backup(target, pages=256, sleep=0.1, progress=progress)
                snapshot.chmod(0o600)
                _live_paper_zero(source)
                if time.monotonic() > deadline:
                    raise Hold("CUSTODY_BACKUP_TIMEOUT")
                snapshot_digest = _sha256(snapshot)
                with snapshot.open("rb") as original, restored.open("xb") as copy:
                    shutil.copyfileobj(original, copy, length=1024 * 1024)
                restored.chmod(0o600)
                restore_digest = _sha256(restored)
                if snapshot_digest != restore_digest:
                    raise Hold("PRIVATE_RESTORE_DIGEST_MISMATCH")
                _verify_restore(restored, deadline)
                if time.monotonic() > deadline:
                    raise Hold("CUSTODY_BACKUP_TIMEOUT")
                continuity = source_continuity(source_before, source_metadata(db_path))
                _release_marker(marker_path)  # Same pinned release after the copy.
                receipt = {"schema": SCHEMA, "state": "PRIVATE_SNAPSHOT_RESTORE_OK",
                           "read_only": False, "source_sql_writes": 0,
                           "shm_locking_possible": True,
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
                manifest = partial / "manifest.json"
                with manifest.open("x", encoding="utf-8") as dest:
                    json.dump(receipt, dest, sort_keys=True)
                manifest.chmod(0o600)
                os.rename(partial, final)
                return receipt
    except sqlite3.Error as exc:
        raise Hold("CUSTODY_SQLITE_UNAVAILABLE") from exc


def sanitize_receipt(mode: str, raw: dict, *, remote_rc: int) -> dict:
    """Runner-side allowlist; refuses unexpected content, never echoes raw text."""
    if mode not in {"PREFLIGHT_ONLY", "CUSTODY_ONCE"}:
        raise Hold("MODE_UNSUPPORTED")
    fallback = {"schema": SCHEMA, "state": "NO_VERIFICADO_HOST_COMMAND"
                if remote_rc else "NO_VERIFICADO_RECEIPT_UNSAFE",
                "read_only": mode == "PREFLIGHT_ONLY",
                "source_sql_writes": None, "paper_zero": "NO_VERIFICADO",
                "ppi_requests": 0, "ppi_watch_touched": False}
    if remote_rc != 0:
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
               "generated_at_utc", "host_mutations", "source_identity_continuity"}
    allowed = shared | (preflight if mode == "PREFLIGHT_ONLY" else custody)
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


def validate_deploy_owner(receipt: dict, comments: list[dict]) -> str:
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
            or _field(body, "Host scope") != "RC6_OBSERVER_ONLINE_CUSTODY_ONCE"):
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


def verify_owner_from_github(receipt_id: str) -> str:
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
    return validate_deploy_owner(receipt, comments)


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    try:
        if arguments == ["host-preflight"]:
            print(json.dumps(host_preflight(), sort_keys=True))
            return 0
        if len(arguments) == 2 and arguments[0] == "host-custody":
            print(json.dumps(host_custody_once(run_id=arguments[1]), sort_keys=True))
            return 0
        if len(arguments) == 2 and arguments[0] == "verify-owner":
            workstream = verify_owner_from_github(arguments[1])
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
            public = sanitize_receipt(mode, raw, remote_rc=int(rc_text))
            with _private_umask(), Path(public_path).open("x", encoding="utf-8") as target:
                json.dump(public, target, sort_keys=True)
            print("RC6_CUSTODY_SANITIZED=" + public["state"])
            return 0
        raise Hold("ACTION_UNSUPPORTED")
    except (Hold, OSError, sqlite3.Error, ValueError, TypeError):
        print("RC6_CUSTODY_FAIL_CLOSED", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
