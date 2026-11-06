#!/usr/bin/env python3
"""A726 physical *read-only* classification; neither a deploy nor recovery.

This script is streamed from reviewed main into a private mount namespace where
the entire Porota data bind is read-only. It never imports runtime code, calls
providers, mutates the host, or reads anything belonging to other applications.
Only exact allowlisted technical fields can leave the runner sanitizer.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import gzip
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import time
import zlib


SCHEMA = "rc6.a726.postfailure-deep-readonly.v1"
DATA = Path("/opt/porota-trading/data")
DB = DATA / "paper_v17/observer_v17.db"
SHADOW = DATA / "paper_v17/artifacts/observer_v17.db/dynamic-shadow"
DEPLOY = DATA / "deploy"
MARKER = DEPLOY / "CURRENT_STATE_V2.json"
MAX_SMALL = 256 * 1024
MAX_CUT = 8 * 1024 * 1024
MAX_PACK = 512 * 1024 * 1024
MAX_PACK_SECONDS = 80
# An *observed historical* gate, not a claim about the R3 package requirement.
REFERENCE_RESERVE_BYTES = 4_080_648_522
HEX40 = re.compile(r"[a-f0-9]{40}\Z")
HEX32 = re.compile(r"[a-f0-9]{32}\Z")
HEX64 = re.compile(r"[a-f0-9]{64}\Z")
IMG = re.compile(r"sha256:[a-f0-9]{64}\Z")
TABLES = {
    "spot": ("paper_positions", "status='OPEN'"),
    "futures": ("paper_future_positions", "status='ACTIVE'"),
    "caucion": ("paper_cauciones", "status='OPEN'"),
    "fci": ("paper_family_lifecycle", "family='FCI' AND state!='REDEEMED'"),
}
TABLE_STATES = {"OBSERVED", "TABLE_ABSENT", "SCHEMA_UNEXPECTED", "QUERY_UNAVAILABLE"}
PACK_STATES = {"NOT_TESTED", "NO_VALID_EXPECTED_HASH", "METADATA_UNSAFE",
               "OVERSIZE", "READ_UNAVAILABLE", "IDENTITY_RACE", "HASH_MATCH",
               "HASH_MISMATCH", "TIMEOUT"}
COLD_PHASES = {"CURRENT_UNAVAILABLE", "CURRENT_BOUND", "REVOKED_OR_UNKNOWN",
               "QUEUE_UNAVAILABLE", "QUEUE_UNBOUND", "RECEIPTS_UNAVAILABLE",
               "RECEIPTS_UNBOUND", "PACK_CLASSIFIED"}


class Hold(ValueError):
    """Private reason never included in the public receipt."""


def _regular(path: Path, limit: int, *, expect_ro: bool = True) -> bytes:
    _safe_parent(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        one = os.fstat(fd)
        if (not stat.S_ISREG(one.st_mode) or one.st_nlink != 1 or
                not 1 <= one.st_size <= limit or
                expect_ro and not os.fstatvfs(fd).f_flag & os.ST_RDONLY):
            raise Hold("FILE_UNSAFE")
        raw = os.read(fd, limit + 1)
        two = os.fstat(fd)
        if (len(raw) != one.st_size or
                _identity(one) != _identity(two)):
            raise Hold("FILE_RACE")
        return raw
    finally:
        os.close(fd)


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _safe_parent(path: Path) -> None:
    """No intermediate symlinks may escape the dedicated Porota data mount."""
    try:
        relative = path.parent.relative_to(DATA)
    except ValueError as exc:
        raise Hold("OUTSIDE_POROTA_DATA") from exc
    current = DATA
    for segment in (".", *relative.parts):
        if segment != ".":
            current = current / segment
        info = current.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise Hold("PARENT_DIRECTORY_UNSAFE")


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def _envelope(path: Path, *, cold: bool = False, compressed: bool = False) -> dict:
    raw = _regular(path, MAX_CUT if compressed else MAX_SMALL)
    if compressed:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as handle:
            raw = handle.read(MAX_CUT + 1)
        if len(raw) > MAX_CUT:
            raise Hold("GZIP_BOUND")
    payload = json.loads(raw)
    digest_key = "sha256" if cold else "digest"
    if (type(payload) is not dict or set(payload) != {"payload", digest_key} or
            type(payload["payload"]) is not dict or
            type(payload[digest_key]) is not str or
            not HEX64.fullmatch(payload[digest_key]) or
            not hmac.compare_digest(
                hashlib.sha256(_canonical(payload["payload"])).hexdigest(),
                payload[digest_key])):
        raise Hold("ENVELOPE_UNBOUND")
    return payload["payload"]


def _marker(path: Path, expected_r1: str, failed_r2: str,
            failed_candidate: str) -> dict:
    result = {"state": "NO_VERIFICADO", "relation": "UNKNOWN",
              "paper_zero": None, "routes_not_called": None}
    try:
        raw = json.loads(_regular(path, MAX_SMALL))
        if (type(raw) is not dict or raw.get("schema_version") != 2 or
                type(raw.get("deploy_sha")) is not str or
                not HEX40.fullmatch(raw["deploy_sha"]) or
                type(raw.get("candidate_sha")) is not str or
                not HEX40.fullmatch(raw["candidate_sha"]) or
                type(raw.get("image_id")) is not str or
                not IMG.fullmatch(raw["image_id"]) or
                raw.get("validation_status") not in
                {"VALIDATED_RUNTIME", "DEPLOYED_VALIDATION_PENDING"}):
            return result
        relation = ("EXPECTED_R1_VALIDATED" if
                    raw["deploy_sha"] == expected_r1 and
                    raw["validation_status"] == "VALIDATED_RUNTIME" else
                    "FAILED_R2_PENDING" if
                    raw["deploy_sha"] == failed_r2 and
                    raw["candidate_sha"] == failed_candidate and
                    raw["validation_status"] == "DEPLOYED_VALIDATION_PENDING" else
                    "OTHER_EXACT_SHA")
        return {"state": "OBSERVED", "relation": relation,
                "paper_zero": (raw.get("mode") == "PRODUCTION_PAPER" and
                               type(raw.get("real_orders_sent")) is int and
                               raw["real_orders_sent"] == 0),
                "routes_not_called": raw.get("real_order_routes") == "NOT_CALLED"}
    except (OSError, Hold, ValueError, TypeError, KeyError):
        return result


def _ledger(db: sqlite3.Connection, table: str, predicate: str,
            required: set[str]) -> dict:
    result = {"state": "QUERY_UNAVAILABLE", "count": None}
    try:
        row = db.execute("SELECT type FROM sqlite_master WHERE name=?", (table,)).fetchone()
        if row is None:
            return {"state": "TABLE_ABSENT", "count": None}
        if row[0] != "table":
            return {"state": "SCHEMA_UNEXPECTED", "count": None}
        # table and predicate are module-level *constants*, never input data.
        columns = {x[1] for x in db.execute("PRAGMA table_info(" + table + ")")}
        if not required.issubset(columns):
            return {"state": "SCHEMA_UNEXPECTED", "count": None}
        count = db.execute("SELECT COUNT(*) FROM " + table + " WHERE " + predicate).fetchone()[0]
        if type(count) is not int or not 0 <= count <= 1_000_000:
            return result
        return {"state": "OBSERVED", "count": count}
    except (sqlite3.Error, TypeError, ValueError, OverflowError):
        return result


def _flat(rows: list[dict]) -> bool | None:
    if any(row["state"] == "OBSERVED" and row["count"] > 0 for row in rows):
        return False
    if all(row["state"] == "OBSERVED" and row["count"] == 0 for row in rows):
        return True
    return None


def _book(path: Path) -> dict:
    result = {"state": "NO_VERIFICADO", "observer": {
        "state": "QUERY_UNAVAILABLE", "mode_paper": None, "orders_zero": None,
        "process": None, "session": None},
        "ledgers": {key: {"state": "QUERY_UNAVAILABLE", "count": None}
                    for key in TABLES},
        "three_ledgers_flat": None, "four_ledgers_flat": None,
        "quiescence_verified": False}
    try:
        _safe_parent(path)
        if not path.is_file() or path.is_symlink() or not os.statvfs(path).f_flag & os.ST_RDONLY:
            raise Hold("DB_NOT_READONLY")
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True,
                                     timeout=.2)) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("PRAGMA busy_timeout=200")
            deadline = time.monotonic() + 4
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            result["state"] = "READ_ONLY_OPENED"
            try:
                row = db.execute("SELECT mode,real_orders_sent,process_state,session_state "
                                 "FROM observer_state WHERE id=1").fetchone()
                if row and type(row[1]) is int:
                    process = row[2] if row[2] in {"RUNNING", "STOPPED", "WAITING_MARKET",
                                                 "DEGRADED", "STARTING"} else "OTHER"
                    session = row[3] if row[3] in {"CLOSED", "MARKET_CLOSED", "OPEN",
                                                 "MARKET_OPEN", "BOOT_CALENDAR",
                                                 "CHECKING"} else "OTHER"
                    result["observer"] = {"state": "OBSERVED",
                        "mode_paper": row[0] == "PRODUCTION_PAPER",
                        "orders_zero": row[1] == 0,
                        "process": process, "session": session}
            except (sqlite3.Error, TypeError, IndexError):
                pass
            for key, (table, predicate) in TABLES.items():
                need = {"status"} if key != "fci" else {"family", "state"}
                result["ledgers"][key] = _ledger(db, table, predicate, need)
            values = result["ledgers"]
            result["three_ledgers_flat"] = _flat([values[k] for k in
                                                   ("spot", "futures", "caucion")])
            result["four_ledgers_flat"] = _flat(list(values.values()))
    except (sqlite3.Error, OSError, Hold, ValueError, TypeError):
        pass
    return result


def _pack_hash(pack: Path, manifest: dict, owner_uid: int,
               max_bytes: int = MAX_PACK, deadline_seconds: int = MAX_PACK_SECONDS) -> str:
    """Hash exactly one CURRENT-bound private pack; no raw data leaves process."""
    expected = manifest.get("pack_sha256")
    if type(expected) is not str or not HEX64.fullmatch(expected):
        return "NO_VALID_EXPECTED_HASH"
    fd = -1
    try:
        _safe_parent(pack)
        fd = os.open(pack, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_uid != owner_uid or
                stat.S_IMODE(before.st_mode) & 0o077 or
                not os.fstatvfs(fd).f_flag & os.ST_RDONLY):
            return "METADATA_UNSAFE"
        if (type(manifest.get("pack_bytes")) is not int or
                manifest["pack_bytes"] != before.st_size or
                manifest.get("pack_identity") != list(_identity(before))):
            return "METADATA_UNSAFE"
        if not 0 < before.st_size <= max_bytes:
            return "OVERSIZE"
        start = time.monotonic()
        digest = hashlib.sha256()
        read = 0
        while read < before.st_size:
            if time.monotonic() - start > deadline_seconds:
                return "TIMEOUT"
            buf = os.read(fd, min(1_048_576, before.st_size - read))
            if not buf:
                return "READ_UNAVAILABLE"
            read += len(buf)
            digest.update(buf)
        after = os.fstat(fd)
        if _identity(before) != _identity(after) or read != before.st_size:
            return "IDENTITY_RACE"
        return ("HASH_MATCH" if hmac.compare_digest(digest.hexdigest(), expected)
                else "HASH_MISMATCH")
    except (OSError, TypeError, ValueError, OverflowError):
        return "READ_UNAVAILABLE"
    finally:
        if fd >= 0:
            os.close(fd)


def _cold(shadow: Path) -> dict:
    result = {"phase": "CURRENT_UNAVAILABLE", "current_hash_bound": None,
              "current_paper_zero": None, "current_routes_blocked": None,
              "reference_bound": None, "source_receipts_bound": None,
              "pack_state": "NOT_TESTED", "pack_hash_verified": False,
              "full_computed_report_verified": False,
              "historical_archive_tested": False}
    try:
        pointer = _envelope(shadow / "CURRENT.json")
        gen = pointer.get("generation_id")
        if (type(gen) is not str or not HEX64.fullmatch(gen) or
                pointer.get("file") != "cut-" + gen + ".json.gz"):
            return result
        cut = _envelope(shadow / pointer["file"], compressed=True)
        manifest = cut.get("manifest")
        reference = cut.get("report", {}).get("computed_report", {})
        if (type(manifest) is not dict or type(reference) is not dict or
                manifest.get("generation_id") != gen or
                manifest.get("sequence") != pointer.get("sequence") or
                type(pointer.get("manifest_sha256")) is not str or
                not HEX64.fullmatch(pointer["manifest_sha256"])):
            return result
        bound = hmac.compare_digest(hashlib.sha256(_canonical(manifest)).hexdigest(),
                                    pointer["manifest_sha256"])
        result["current_hash_bound"] = bound
        order_count = cut.get("status", {}).get("real_orders_sent")
        result["current_paper_zero"] = type(order_count) is int and order_count == 0
        result["current_routes_blocked"] = cut.get("status", {}).get("real_routes") == "NOT_CALLED"
        result["phase"] = "CURRENT_BOUND"
        snapshot = reference.get("snapshot_id")
        if (not bound or type(snapshot) is not str or not HEX32.fullmatch(snapshot) or
                snapshot in {"89e7f81dbc9b4a628bdc30edf86a17b0",
                             "7dc99ccd8f7c47849ca3705ae1cb2c49"} or
                gen in {"b6687dbca06ab97bc9c93d3f7940c61bba577f68d4ff4b9f887332638c0b7cb8",
                        "af86116a3a64a9214cfde7ccfc1e04d3165270cf6d6f6ae2b4f4ce6e13c89ad9"}):
            result["phase"] = "REVOKED_OR_UNKNOWN"
            return result
        source = shadow / "cold-audit"
        _safe_parent(source / "queue.json")
        info = source.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or \
                stat.S_IMODE(info.st_mode) & 0o077:
            raise Hold("PRIVATE_SOURCE_DIRECTORY_UNSAFE")
        result["phase"] = "QUEUE_UNAVAILABLE"
        queue = _envelope(source / "queue.json", cold=True)
        if queue.get("state") != "FAILED" or queue.get("snapshot_id") != snapshot:
            result["phase"] = "QUEUE_UNBOUND"
            return result
        result["phase"] = "RECEIPTS_UNAVAILABLE"
        cold_manifest = _envelope(source / ("manifest-" + snapshot + ".json"), cold=True)
        receipt = _envelope(source / ("receipt-" + snapshot + ".json"), cold=True)
        manifest_sha = hashlib.sha256(_canonical(cold_manifest)).hexdigest()
        reference_bound = (reference.get("schema") == "rc6.computed-audit-reference.v2" and
                           reference.get("state") == "PENDING" and
                           reference.get("snapshot_id") == snapshot and
                           reference.get("manifest_sha256") == manifest_sha)
        result["reference_bound"] = reference_bound
        receipts_bound = (queue.get("manifest_sha256") == manifest_sha and
                          cold_manifest.get("snapshot_id") == snapshot and
                          cold_manifest.get("pack") == snapshot + ".pack" and
                          cold_manifest.get("epoch") == "rc6.audit-actual-values.v1" and
                          receipt.get("state") == "FAILED" and
                          receipt.get("snapshot_id") == snapshot and
                          receipt.get("manifest_sha256") == manifest_sha)
        result["source_receipts_bound"] = receipts_bound
        if not reference_bound or not receipts_bound:
            result["phase"] = "RECEIPTS_UNBOUND"
            return result
        owner = info.st_uid
        state = _pack_hash(source / (snapshot + ".pack"), cold_manifest, owner)
        result["pack_state"] = state
        result["pack_hash_verified"] = state == "HASH_MATCH"
        result["phase"] = "PACK_CLASSIFIED"
    except (OSError, Hold, ValueError, TypeError, KeyError, AttributeError,
            gzip.BadGzipFile, EOFError, zlib.error):
        pass
    return result


def _disk() -> dict:
    result = {"state": "NO_VERIFICADO", "root_free_bytes": None,
              "docker_free_bytes": None, "same_filesystem": None,
              "historical_reserve_bytes": REFERENCE_RESERVE_BYTES,
              "historical_reserve_met": None,
              "r3_pretransfer_sufficient": "NO_VERIFICADO"}
    try:
        root = os.statvfs("/")
        root_free = root.f_bavail * root.f_frsize
        proc = subprocess.run(["docker", "info", "--format", "{{.DockerRootDir}}"],
                              capture_output=True, text=True, check=False, timeout=5)
        if proc.returncode or not 1 <= len(proc.stdout) <= 256:
            return result
        docker_root = proc.stdout.strip()
        if not docker_root.startswith("/") or not os.path.isdir(docker_root):
            return result
        st = os.statvfs(docker_root)
        docker_free = st.f_bavail * st.f_frsize
        if min(root_free, docker_free) < 0:
            return result
        return {"state": "OBSERVED", "root_free_bytes": root_free,
                "docker_free_bytes": docker_free,
                "same_filesystem": os.stat("/").st_dev == os.stat(docker_root).st_dev,
                "historical_reserve_bytes": REFERENCE_RESERVE_BYTES,
                "historical_reserve_met": root_free >= REFERENCE_RESERVE_BYTES and
                                           docker_free >= REFERENCE_RESERVE_BYTES,
                "r3_pretransfer_sufficient": "NO_VERIFICADO"}
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        return result


def _ro_mount() -> bool:
    try:
        return bool(os.statvfs(DATA).f_flag & os.ST_RDONLY)
    except OSError:
        return False


def observe(expected_r1: str, failed_r2: str, candidate: str) -> dict:
    if not _ro_mount():
        return {"schema": SCHEMA, "state": "NO_VERIFICADO", "read_only": True}
    return {"schema": SCHEMA, "state": "DIAGNOSTIC_ONLY", "read_only": True,
            "ro_mount_verified": True, "mutations": 0, "provider_gets": 0,
            "routes_called": False, "runtime_validated": False,
            "marker": _marker(MARKER, expected_r1, failed_r2, candidate),
            "book": _book(DB), "cold": _cold(SHADOW), "disk": _disk()}


def sanitize(raw: str, expected_r1: str, failed_r2: str,
             failed_candidate: str) -> dict:
    fallback = {"schema": SCHEMA, "state": "NO_VERIFICADO", "read_only": True,
                "mutations": 0, "provider_gets": 0, "runtime_validated": False}
    try:
        if len(raw) > 8192 or any(not HEX40.fullmatch(x) for x in
                                  (expected_r1, failed_r2, failed_candidate)):
            raise Hold("BOUND")
        src = json.loads(raw)
        required = {"schema", "state", "read_only", "ro_mount_verified",
                    "mutations", "provider_gets", "routes_called",
                    "runtime_validated", "marker", "book", "cold", "disk"}
        if (type(src) is not dict or set(src) != required or src["schema"] != SCHEMA or
                src["state"] != "DIAGNOSTIC_ONLY" or src["read_only"] is not True or
                src["ro_mount_verified"] is not True or type(src["mutations"]) is not int or
                src["mutations"] != 0 or type(src["provider_gets"]) is not int or
                src["provider_gets"] != 0 or src["routes_called"] is not False or
                src["runtime_validated"] is not False):
            raise Hold("TOPLEVEL_INVALID")
        marker = src["marker"]
        if (type(marker) is not dict or set(marker) !=
                {"state", "relation", "paper_zero", "routes_not_called"} or
                marker["state"] not in {"OBSERVED", "NO_VERIFICADO"} or
                marker["relation"] not in {"UNKNOWN", "EXPECTED_R1_VALIDATED",
                                          "FAILED_R2_PENDING", "OTHER_EXACT_SHA"} or
                any(marker[k] is not None and type(marker[k]) is not bool for k in
                    ("paper_zero", "routes_not_called"))):
            raise Hold("MARKER_INVALID")
        book = src["book"]
        if (type(book) is not dict or set(book) !=
                {"state", "observer", "ledgers", "three_ledgers_flat",
                 "four_ledgers_flat", "quiescence_verified"} or
                book["state"] not in {"NO_VERIFICADO", "READ_ONLY_OPENED"} or
                book["quiescence_verified"] is not False or
                any(book[k] is not None and type(book[k]) is not bool for k in
                    ("three_ledgers_flat", "four_ledgers_flat"))):
            raise Hold("BOOK_INVALID")
        observer = book["observer"]
        if (type(observer) is not dict or set(observer) !=
                {"state", "mode_paper", "orders_zero", "process", "session"} or
                observer["state"] not in {"OBSERVED", "QUERY_UNAVAILABLE"} or
                any(observer[k] is not None and type(observer[k]) is not bool for k in
                    ("mode_paper", "orders_zero")) or
                observer["process"] not in {None, "RUNNING", "STOPPED",
                    "WAITING_MARKET", "DEGRADED", "STARTING", "OTHER"} or
                observer["session"] not in {None, "CLOSED", "MARKET_CLOSED",
                    "OPEN", "MARKET_OPEN", "BOOT_CALENDAR", "CHECKING", "OTHER"}):
            raise Hold("OBSERVER_INVALID")
        ledger = book["ledgers"]
        if type(ledger) is not dict or set(ledger) != set(TABLES):
            raise Hold("LEDGERS_INVALID")
        for row in ledger.values():
            if (type(row) is not dict or set(row) != {"state", "count"} or
                    row["state"] not in TABLE_STATES or
                    (row["state"] == "OBSERVED" and
                     (type(row["count"]) is not int or not 0 <= row["count"] <= 1_000_000)) or
                    (row["state"] != "OBSERVED" and row["count"] is not None)):
                raise Hold("LEDGER_ROW_INVALID")
        if (book["three_ledgers_flat"] != _flat([ledger[k] for k in
                ("spot", "futures", "caucion")]) or
                book["four_ledgers_flat"] != _flat(list(ledger.values()))):
            raise Hold("FLAT_PREDICATE_INVALID")
        cold = src["cold"]
        if (type(cold) is not dict or set(cold) !=
                {"phase", "current_hash_bound", "current_paper_zero",
                 "current_routes_blocked", "reference_bound", "source_receipts_bound",
                 "pack_state", "pack_hash_verified",
                 "full_computed_report_verified", "historical_archive_tested"} or
                cold["phase"] not in COLD_PHASES or cold["pack_state"] not in PACK_STATES or
                cold["full_computed_report_verified"] is not False or
                cold["historical_archive_tested"] is not False or
                type(cold["pack_hash_verified"]) is not bool or
                cold["pack_hash_verified"] != (cold["pack_state"] == "HASH_MATCH") or
                any(cold[k] is not None and type(cold[k]) is not bool for k in
                    ("current_hash_bound", "current_paper_zero", "current_routes_blocked",
                     "reference_bound", "source_receipts_bound"))):
            raise Hold("COLD_INVALID")
        disk = src["disk"]
        if (type(disk) is not dict or set(disk) !=
                {"state", "root_free_bytes", "docker_free_bytes", "same_filesystem",
                 "historical_reserve_bytes", "historical_reserve_met",
                 "r3_pretransfer_sufficient"} or
                disk["state"] not in {"OBSERVED", "NO_VERIFICADO"} or
                type(disk["historical_reserve_bytes"]) is not int or
                disk["historical_reserve_bytes"] != REFERENCE_RESERVE_BYTES or
                disk["r3_pretransfer_sufficient"] != "NO_VERIFICADO"):
            raise Hold("DISK_INVALID")
        if disk["state"] == "OBSERVED":
            if (any(type(disk[k]) is not int or not 0 <= disk[k] <= 10**15 for k in
                    ("root_free_bytes", "docker_free_bytes")) or
                    type(disk["same_filesystem"]) is not bool or
                    type(disk["historical_reserve_met"]) is not bool or
                    disk["historical_reserve_met"] !=
                    (min(disk["root_free_bytes"], disk["docker_free_bytes"]) >=
                     REFERENCE_RESERVE_BYTES)):
                raise Hold("DISK_ARITHMETIC_INVALID")
        elif any(disk[k] is not None for k in
                 ("root_free_bytes", "docker_free_bytes", "same_filesystem",
                  "historical_reserve_met")):
            raise Hold("DISK_UNVERIFIED_INVALID")
        # Explicit, fresh exact dictionaries: no nested input passthrough.
        return {"schema": SCHEMA, "state": "DIAGNOSTIC_ONLY", "read_only": True,
                "mutations": 0, "provider_gets": 0, "runtime_validated": False,
                "expected_previous_sha": expected_r1,
                "failed_deploy_sha": failed_r2,
                "failed_candidate_sha": failed_candidate,
                "marker": {k: marker[k] for k in
                           ("state", "relation", "paper_zero", "routes_not_called")},
                "book": {"state": book["state"],
                         "observer": {k: observer[k] for k in
                                      ("state", "mode_paper", "orders_zero", "process", "session")},
                         "ledgers": {name: {"state": ledger[name]["state"],
                                            "count": ledger[name]["count"]}
                                     for name in TABLES},
                         "three_ledgers_flat": book["three_ledgers_flat"],
                         "four_ledgers_flat": book["four_ledgers_flat"],
                         "quiescence_verified": False},
                "cold": {k: cold[k] for k in
                         ("phase", "current_hash_bound", "current_paper_zero",
                          "current_routes_blocked", "reference_bound",
                          "source_receipts_bound", "pack_state",
                          "pack_hash_verified", "full_computed_report_verified",
                          "historical_archive_tested")},
                "disk": {k: disk[k] for k in
                         ("state", "root_free_bytes", "docker_free_bytes",
                          "same_filesystem", "historical_reserve_bytes",
                          "historical_reserve_met", "r3_pretransfer_sufficient")}}
    except (OSError, ValueError, Hold, TypeError, KeyError, OverflowError):
        return fallback


def main() -> int:
    if "--sanitize" in sys.argv:
        receipt = sanitize(sys.stdin.read(8193),
            os.environ.get("RC6_RO_PREVIOUS_SHA", ""),
            os.environ.get("RC6_RO_FAILED_DEPLOY_SHA", ""),
            os.environ.get("RC6_RO_FAILED_CANDIDATE_SHA", ""))
        print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
        return 0 if receipt["state"] == "DIAGNOSTIC_ONLY" else 2
    if "--host" not in sys.argv:
        return 2
    expected = [os.environ.get(name, "") for name in
        ("RC6_RO_PREVIOUS_SHA", "RC6_RO_FAILED_DEPLOY_SHA",
         "RC6_RO_FAILED_CANDIDATE_SHA")]
    if any(not HEX40.fullmatch(value) for value in expected) or len(set(expected)) != 3:
        return 2
    record = observe(*expected)
    print(json.dumps(record, sort_keys=True, separators=(",", ":")))
    return 0 if record["state"] == "DIAGNOSTIC_ONLY" else 2


if __name__ == "__main__":
    sys.exit(main())
