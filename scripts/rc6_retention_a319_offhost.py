#!/usr/bin/env python3
"""Fail-closed, OFFHOST-ONLY RC6 history retention rehearsal.

This module accepts independently hash-pinned *copies*, never a host path. It
enumerates explicit/possible SQLite references, recursive JSON references,
artifact/replay references, and source-capture evidence before selecting rows
for a 365-ART-date deletion rehearsal in a second disposable SQLite copy.

An offline manifest can prove internal consistency, not that every external
consumer or the historical time of an observation is authentic. Consequently
this program never grants host purge authority, even after a clean rehearsal.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import tempfile
import time
from urllib.parse import quote
from zoneinfo import ZoneInfo


ART = ZoneInfo("America/Argentina/Buenos_Aires")
SCHEMA = "RC6_RETENTION_A319_OFFHOST_V1"
MANIFEST_SCHEMA = "RC6_RETENTION_A319_MANIFEST_V1"
CAPTURE_SCHEMA = "RC6_RETENTION_A319_CAPTURE_V1"
REQUIRED_KINDS = frozenset({"sqlite", "json", "artifact", "replay"})
MAX_BYTES = 2 * 1024**3
MAX_JSON_DOCUMENT = 128 * 1024**2
MAX_JSON_NODES = 1_000_000
MAX_TABLES = 256
MAX_ROWS = 1_000_000
SHA = re.compile(r"[0-9a-f]{64}\Z")
IDENTITY = ("id", "symbol", "instrument_type", "market", "settlement",
            "date", "source", "metadata_json")
PROVENANCE = ("currency", "price_basis", "adjustment_basis", "known_at_utc")


class EvidenceError(ValueError):
    """Named, non-sensitive rejection that cannot authorize host deletion."""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise EvidenceError(reason)


def utc(value: str) -> datetime:
    require(isinstance(value, str), "TIME_NOT_A_STRING")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceError("TIME_INVALID") from exc
    require(parsed.tzinfo is not None and parsed.utcoffset() is not None,
            "TIME_UNZONED")
    return parsed.astimezone(timezone.utc)


def date_iso(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def stable_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def row_digest(row: dict) -> str:
    """Stable identity of the original row *including* its untouched metadata."""
    return digest_bytes(stable_json({key: row[key] for key in IDENTITY}))


def copy_inside(root: Path, value: str, *, max_bytes: int = MAX_BYTES) -> Path:
    require(isinstance(value, str) and value and not Path(value).is_absolute(),
            "COPY_PATH_NOT_RELATIVE")
    relative = Path(value)
    require(".." not in relative.parts and "." not in relative.parts,
            "COPY_PATH_TRAVERSAL")
    path = root / relative
    require(path.resolve().is_relative_to(root), "COPY_PATH_OUTSIDE_RUNNER")
    require(not path.is_symlink() and path.is_file(), "COPY_NOT_REGULAR")
    require(not any(parent.is_symlink() for parent in path.parents if parent != root),
            "COPY_PARENT_ALIAS")
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
            "COPY_HARDLINK_OR_SPECIAL")
    require(1 <= info.st_size <= max_bytes, "COPY_BYTE_BUDGET")
    require(not any(Path(str(path) + suffix).exists()
                    for suffix in ("-wal", "-shm", "-journal")),
            "COPY_HAS_SQLITE_SIDECAR")
    return path


def sha_file(path: Path, deadline: float) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024**2), b""):
            require(time.monotonic() < deadline, "AUDIT_TIME_BUDGET")
            result.update(chunk)
    return result.hexdigest()


def secure_open(root: Path, relative: str) -> int:
    """Open each path component beneath the root by dirfd; forbid symlink races."""
    require(Path("/proc/self/fd").is_dir(), "LINUX_FD_PIN_REQUIRED")
    parts = Path(relative).parts
    require(parts and all(part not in (".", "..") for part in parts),
            "COPY_PATH_TRAVERSAL")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    require(nofollow != 0, "NOFOLLOW_NOT_SUPPORTED")
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | nofollow)
    try:
        for part in parts[:-1]:
            next_dir = os.open(part, os.O_RDONLY | os.O_DIRECTORY | nofollow,
                               dir_fd=directory)
            os.close(directory)
            directory = next_dir
        return os.open(parts[-1], os.O_RDONLY | os.O_NONBLOCK | nofollow,
                       dir_fd=directory)
    except OSError as exc:
        raise EvidenceError("COPY_SECURE_OPEN_DENIED") from exc
    finally:
        os.close(directory)


def pinned_copy(root: Path, spec: dict, deadline: float, stack: ExitStack,
                held: list[tuple[Path, Path, tuple[int, ...], str]]) -> Path:
    require(isinstance(spec, dict), "SNAPSHOT_SCHEMA")
    expected = spec.get("sha256")
    require(isinstance(expected, str) and SHA.fullmatch(expected) is not None,
            "SNAPSHOT_SHA_MISSING")
    path = copy_inside(root, spec.get("path"))
    before = path.stat()
    identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    fd = secure_open(root, spec["path"])
    handle = stack.enter_context(os.fdopen(fd, "rb"))
    stable_path = Path("/proc/self/fd") / str(handle.fileno())
    during = os.fstat(handle.fileno())
    require((during.st_dev, during.st_ino, during.st_size, during.st_mtime_ns) == identity,
            "SNAPSHOT_CHANGED_BEFORE_FD_PIN")
    require(sha_file(stable_path, deadline) == expected, "SNAPSHOT_HASH_MISMATCH")
    after = os.fstat(handle.fileno())
    require((after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) == identity,
            "SNAPSHOT_CHANGED_DURING_HASH")
    held.append((path, stable_path, identity, expected))
    return stable_path


def open_ro(path: Path):
    uri = "file:" + quote(str(path), safe="/") + "?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True, timeout=.2)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    return connection


def quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def looks_version_ref(name: str) -> bool:
    lowered = name.lower()
    return "version" in lowered and any(mark in lowered for mark in ("id", "pk", "ref"))


def version_reference(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is int and value >= 0:
        return value
    if (isinstance(value, str) and len(value) <= 19
            and value.isascii() and value.isdecimal()):
        return int(value)
    if type(value) is float and value.is_integer() and 0 <= value <= 2**53:
        return int(value)
    raise EvidenceError("AMBIGUOUS_VERSION_REFERENCE")


def load_strict_json(value: str | bytes) -> object:
    def unique_keys(pairs):
        result = {}
        for key, item in pairs:
            require(key not in result, "JSON_DUPLICATE_KEY")
            result[key] = item
        return result

    def nonfinite(_):
        raise EvidenceError("JSON_NONFINITE")

    try:
        return json.loads(value, object_pairs_hook=unique_keys,
                          parse_constant=nonfinite)
    except EvidenceError:
        raise
    except (RecursionError, UnicodeError, ValueError) as exc:
        raise EvidenceError("JSON_SNAPSHOT_UNREADABLE") from exc


def json_refs(node: object, known_ids: set[int], *, key: str = "",
              deadline: float = float("inf")) -> set[int]:
    """Conservatively pin matching IDs in version-shaped JSON fields."""
    matches: set[int] = set()
    stack = [(node, key, 0)]
    seen = 0
    while stack:
        current, context, depth = stack.pop()
        seen += 1
        require(seen <= MAX_JSON_NODES, "JSON_NODE_BUDGET")
        require(depth <= 128, "JSON_DEPTH_BUDGET")
        if seen % 10000 == 0:
            require(time.monotonic() < deadline, "AUDIT_TIME_BUDGET")
        if isinstance(current, dict):
            stack.extend((value, context + "." + str(name).lower(), depth + 1)
                         for name, value in current.items())
        elif isinstance(current, list):
            stack.extend((value, context, depth + 1) for value in current)
        elif ("version" in context and
              (any(mark in context for mark in ("id", "pk", "ref"))
               or context.endswith(("version", "versions")))
              and not isinstance(current, (dict, list))):
            number = version_reference(current)
            if number in known_ids:
                matches.add(number)
        elif (isinstance(current, str) and context.endswith(("json", "payload"))
              and current.startswith(("{", "[")) and len(current) < 1024**2):
            stack.append((load_strict_json(current), context, depth + 1))
    return matches


def scan_json(path: Path, known_ids: set[int], deadline: float,
              *, original_name: str = "") -> tuple[set[int], int]:
    require(time.monotonic() < deadline, "AUDIT_TIME_BUDGET")
    if Path(original_name).suffix.lower() in (".jsonl", ".ndjson"):
        matches: set[int] = set()
        with path.open("rb") as handle:
            for line_number, line in enumerate(handle, start=1):
                require(line_number <= MAX_ROWS and len(line) <= MAX_JSON_DOCUMENT,
                        "JSON_LINE_BUDGET")
                require(time.monotonic() < deadline, "AUDIT_TIME_BUDGET")
                if line.strip():
                    matches |= json_refs(load_strict_json(line), known_ids,
                                         deadline=deadline)
        return matches, 1
    require(path.stat().st_size <= MAX_JSON_DOCUMENT, "JSON_DOCUMENT_BUDGET")
    obj = load_strict_json(path.read_bytes())
    return json_refs(obj, known_ids, deadline=deadline), 1


def scan_sqlite(path: Path, known_ids: set[int], deadline: float,
                *, exclude_history: bool = False) -> tuple[set[int], int]:
    references: set[int] = set()
    examined = 0
    with open_ro(path) as db:
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 10000)
        require(db.execute("PRAGMA quick_check").fetchone()[0] == "ok",
                "SOURCE_SQLITE_QUICK_CHECK")
        require(db.execute("PRAGMA foreign_key_check").fetchone() is None,
                "SOURCE_SQLITE_FOREIGN_KEY_CHECK")
        tables = [row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        require(len(tables) <= MAX_TABLES, "SQLITE_TABLE_BUDGET")
        for name in sorted(tables):
            if exclude_history and name == "history_versions_v2":
                continue
            require(time.monotonic() < deadline, "AUDIT_TIME_BUDGET")
            columns = [row[1] for row in db.execute(
                f"PRAGMA table_info({quoted(name)})")]
            fk = {row[3] for row in db.execute(
                f"PRAGMA foreign_key_list({quoted(name)})")
                if row[2].lower() == "history_versions_v2"}
            relevant = [col for col in columns if
                        col in fk or looks_version_ref(col) or
                        col.lower().endswith(("json", "_payload", "_receipt"))]
            if not relevant:
                continue
            projection = ",".join(quoted(col) for col in relevant)
            for row in db.execute(f"SELECT {projection} FROM {quoted(name)}"):
                examined += 1
                require(examined <= MAX_ROWS, "REFERENCE_ROW_BUDGET")
                for col, value in zip(relevant, row):
                    if value is None:
                        continue
                    if col in fk or looks_version_ref(col):
                        number = version_reference(value)
                        if number in known_ids:
                            references.add(number)
                    else:
                        try:
                            obj = load_strict_json(value)
                        except EvidenceError as exc:
                            if str(exc) == "JSON_SNAPSHOT_UNREADABLE":
                                raise EvidenceError("JSON_COLUMN_UNCLASSIFIABLE") from exc
                            raise
                        except (TypeError, ValueError) as exc:
                            raise EvidenceError("JSON_COLUMN_UNCLASSIFIABLE") from exc
                        references |= json_refs(obj, known_ids, deadline=deadline)
    return references, examined


def legacy_rows(path: Path, deadline: float) -> list[dict]:
    rows: list[dict] = []
    with open_ro(path) as db:
        cols = {row[1] for row in db.execute(
            'PRAGMA table_info("history_versions_v2")')}
        require(set(IDENTITY) <= cols, "LEGACY_IDENTITY_SCHEMA")
        q = ",".join(quoted(col) for col in IDENTITY)
        for values in db.execute(
                f'SELECT {q} FROM "history_versions_v2" ORDER BY id'):
            require(time.monotonic() < deadline and len(rows) < MAX_ROWS,
                    "LEGACY_ROW_BUDGET")
            row = dict(zip(IDENTITY, values))
            require(type(row["id"]) is int and row["id"] > 0,
                    "LEGACY_ID_INVALID")
            require(all(value is None or type(value) in (str, int, float)
                        for value in row.values()), "LEGACY_UNSUPPORTED_FIELD_TYPE")
            if rows:
                require(rows[-1]["id"] < row["id"], "LEGACY_ID_DUPLICATE")
            rows.append(row)
    return rows


def provenance_ok(row: dict, claim: dict, captures: dict[str, dict],
                  at: datetime) -> bool:
    """Only structural consistency; authentic historical provenance needs a separate issuer."""
    if not isinstance(claim, dict) or claim.get("version_id") != row["id"]:
        return False
    if claim.get("legacy_row_sha256") != row_digest(row):
        return False
    if not all(isinstance(claim.get(k), str) and claim[k].strip()
               for k in PROVENANCE):
        return False
    if any(claim[k].strip().upper() in
           {"UNKNOWN", "NO_VERIFICADO", "TBD", "TODO", "INFERRED"}
           for k in PROVENANCE):
        return False
    if not all(isinstance(row.get(k), str) and row[k].strip()
               for k in ("symbol", "instrument_type", "market",
                         "settlement", "date", "source")):
        return False
    if row["source"].strip() != claim.get("provider"):
        return False
    try:
        metadata = load_strict_json(row["metadata_json"])
        if not isinstance(metadata, dict):
            return False
        namespaces = [metadata]
        if isinstance(metadata.get("metadata"), dict):
            namespaces.append(metadata["metadata"])
        for field in ("currency", "price_basis", "adjustment_basis"):
            if any(space.get(field) not in (None, "", claim[field])
                   for space in namespaces):
                return False
    except (EvidenceError, TypeError):
        return False
    try:
        when = utc(claim["known_at_utc"])
        captured = captures[claim["capture_path"]]
        observed = utc(captured["captured_at_utc"])
    except (KeyError, EvidenceError, TypeError):
        return False
    if when > at or observed > when:
        return False
    if claim.get("provider") != captured.get("provider"):
        return False
    fingerprint = (row_digest(row), *(claim[k] for k in PROVENANCE))
    if fingerprint not in captured["_fingerprints"]:
        return False
    return True


def copy_simulation(source: Path, candidate_ids: list[int], root: Path,
                    original_sha: str, deadline: float) -> dict:
    """DELETE only in a second tempfile copy; verify source and replay twice."""
    require(candidate_ids and len(candidate_ids) <= MAX_ROWS,
            "DRY_RUN_IDS_INVALID")
    before_size = source.stat().st_size
    reserve = shutil.disk_usage(root).free
    require(reserve > before_size * 3 + 128 * 1024**2,
            "DRY_RUN_SCRATCH_RESERVE")
    outcomes = []
    rollback_hashes = []
    with tempfile.TemporaryDirectory(prefix="rc6-a319-copy-only-", dir=root) as name:
        scratch = Path(name)
        for index in range(2):
            require(time.monotonic() < deadline, "AUDIT_TIME_BUDGET")
            clone = scratch / f"trial-{index}.sqlite"
            shutil.copyfile(source, clone)
            require(sha_file(clone, deadline) == original_sha,
                    "DRY_RUN_BASELINE_HASH_MISMATCH")
            with sqlite3.connect(clone, timeout=1) as db:
                db.execute("PRAGMA foreign_keys=ON")
                db.execute("PRAGMA trusted_schema=OFF")
                db.execute("BEGIN IMMEDIATE")
                initial_changes = db.total_changes
                deleted = 0
                for offset in range(0, len(candidate_ids), 1000):
                    chunk = candidate_ids[offset:offset + 1000]
                    marks = ",".join("?" for _ in chunk)
                    deleted += db.execute(
                        f'DELETE FROM "history_versions_v2" WHERE id IN ({marks})',
                        chunk).rowcount
                require(deleted == len(candidate_ids), "DRY_RUN_DELETE_COUNT_MISMATCH")
                require(db.total_changes - initial_changes == deleted,
                        "DRY_RUN_CASCADE_OR_TRIGGER_MUTATION")
                db.commit()
                for offset in range(0, len(candidate_ids), 1000):
                    chunk = candidate_ids[offset:offset + 1000]
                    marks = ','.join('?' for _ in chunk)
                    second = db.execute(
                        'SELECT COUNT(*) FROM "history_versions_v2" '
                        f'WHERE id IN ({marks})', chunk).fetchone()[0]
                    require(second == 0, "DRY_RUN_NONIDEMPOTENT")
                    replay_deleted = db.execute(
                        f'DELETE FROM "history_versions_v2" WHERE id IN ({marks})',
                        chunk).rowcount
                    require(replay_deleted == 0, "DRY_RUN_REDELETE_NONIDEMPOTENT")
                require(db.execute("PRAGMA quick_check").fetchone()[0] == "ok",
                        "DRY_RUN_SQLITE_QUICK_CHECK")
                require(not db.execute("PRAGMA foreign_key_check").fetchone(),
                        "DRY_RUN_FOREIGN_KEY_CHECK")
                digest = hashlib.sha256()
                for row in db.execute('SELECT * FROM "history_versions_v2" ORDER BY id'):
                    digest.update(stable_json(row) + b"\n")
                outcomes.append((deleted, digest.hexdigest()))
            # Reversible COPY restoration, not an assertion that host DELETE is reversible.
            shutil.copyfile(source, clone)
            rollback_hashes.append(sha_file(clone, deadline))
            require(time.monotonic() < deadline, "AUDIT_TIME_BUDGET")
        require(outcomes[0] == outcomes[1], "DRY_RUN_NONDETERMINISTIC_REPLAY")
        require(all(value == original_sha for value in rollback_hashes),
                "DRY_RUN_COPY_RESTORE_FAILED")
    require(sha_file(source, deadline) == original_sha,
            "SOURCE_CHANGED_AFTER_SIMULATION")
    return {"delete_trial_repeated_twice": True,
            "business_replay_semantics_verified": False,
            "deleted_in_copies_only": outcomes[0][0],
            "retained_rows_sha256": outcomes[0][1],
            "copy_rollback_restored_twice": True,
            "source_sha256_unchanged": True, "host_mutations": 0}


def write_index(root: Path, name: str, rows: list[dict], decisions: dict[int, str],
                pin_sources: dict[int, set[str]], pit_states: dict[int, str],
                deadline: float) -> dict:
    """Exclusive, non-sensitive complete row ledger on the OFFHOST runner."""
    require(isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", name)
            and name not in (".", ".."), "INDEX_FILENAME_INVALID")
    target = root / name
    require(not target.exists() and not target.is_symlink(), "INDEX_ALREADY_EXISTS")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(target, flags, 0o600)
    except OSError as exc:
        raise EvidenceError("INDEX_CREATION_DENIED") from exc
    digest = hashlib.sha256()
    try:
        with os.fdopen(descriptor, "wb") as output:
            for row in rows:
                require(time.monotonic() < deadline, "INDEX_TIME_BUDGET")
                version_id = row["id"]
                record = {
                    "schema": SCHEMA, "version_id": version_id,
                    "legacy_row_sha256": row_digest(row),
                    "decision": decisions[version_id],
                    "pin_kinds": sorted(pin_sources.get(version_id, set())),
                    "point_in_time": pit_states[version_id],
                    "provider_authenticity": "NO_VERIFICADO",
                    "host_purge_allowed": False,
                }
                line = stable_json(record) + b"\n"
                output.write(line)
                digest.update(line)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        target.unlink(missing_ok=True)  # only the O_EXCL file created by this call
        raise
    return {"path": name, "sha256": digest.hexdigest(),
            "rows": len(rows), "contains_raw_market_data": False}


def _audit(root: Path, database: str, database_sha256: str,
           manifest_path: str, manifest_sha256: str, as_of_utc: str,
           *, simulate: bool, budget_seconds: int, index_output: str | None,
           stack: ExitStack, held: list) -> dict:
    """Produce a sanitized count/quality receipt; never a host deletion manifest."""
    root = Path(root).resolve()
    require(root.is_dir() and not root.is_symlink(), "RUNNER_ROOT_INVALID")
    require(not root.is_relative_to(Path('/opt/porota-trading')),
            "HOST_ROOT_FORBIDDEN")
    require(0 < budget_seconds <= 600, "AUDIT_BUDGET_INVALID")
    deadline = time.monotonic() + budget_seconds
    require(isinstance(database_sha256, str) and SHA.fullmatch(database_sha256),
            "DATABASE_SHA_MISSING")
    require(isinstance(manifest_sha256, str) and SHA.fullmatch(manifest_sha256),
            "MANIFEST_SHA_MISSING")
    source = pinned_copy(root, {"path": database, "sha256": database_sha256},
                         deadline, stack, held)
    manifest_file = pinned_copy(root, {"path": manifest_path,
                                       "sha256": manifest_sha256}, deadline, stack, held)
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise EvidenceError("MANIFEST_UNREADABLE") from exc
    require(isinstance(manifest, dict) and manifest.get("schema") == MANIFEST_SCHEMA,
            "MANIFEST_SCHEMA_INVALID")
    snapshot_specs = manifest.get("snapshots")
    require(isinstance(snapshot_specs, list) and len(snapshot_specs) <= 80,
            "MANIFEST_SNAPSHOT_BUDGET")
    kinds = set()
    snapshot_paths: set[str] = set()
    verified: dict[str, tuple[str, Path]] = {}
    for snap in snapshot_specs:
        require(isinstance(snap, dict) and snap.get("kind") in
                REQUIRED_KINDS | {"provider_capture"}, "SNAPSHOT_KIND_INVALID")
        kind, relative = snap["kind"], snap.get("path")
        require(relative not in snapshot_paths and relative not in (database, manifest_path),
                "SNAPSHOT_DUPLICATE_OR_SELF")
        path = pinned_copy(root, snap, deadline, stack, held)
        snapshot_paths.add(relative)
        kinds.add(kind)
        verified[relative] = (kind, path)
    all_kinds = REQUIRED_KINDS <= kinds
    at = utc(as_of_utc)
    cutoff = at.astimezone(ART).date() - timedelta(days=364)
    rows = legacy_rows(source, deadline)
    known_ids = {row["id"] for row in rows}
    pins: set[int] = set()
    pin_sources: dict[int, set[str]] = {}

    def pin(values: set[int], kind: str) -> None:
        pins.update(values)
        for version_id in values:
            pin_sources.setdefault(version_id, set()).add(kind)

    refs, local_count = scan_sqlite(source, known_ids, deadline,
                                    exclude_history=True)
    pin(refs, "sqlite_local")
    scanned = Counter({"sqlite_local_rows": local_count})
    captures: dict[str, dict] = {}
    for relative, (kind, path) in verified.items():
        if kind == "provider_capture":
            require(path.stat().st_size <= MAX_JSON_DOCUMENT,
                    "PROVIDER_CAPTURE_DOCUMENT_BUDGET")
            capture = load_strict_json(path.read_bytes())
            require(isinstance(capture, dict) and capture.get("schema") == CAPTURE_SCHEMA,
                    "PROVIDER_CAPTURE_SCHEMA")
            require(isinstance(capture.get("provider"), str) and capture["provider"],
                    "PROVIDER_CAPTURE_ISSUER_MISSING")
            utc(capture.get("captured_at_utc"))
            capture_rows = capture.get("rows")
            require(isinstance(capture_rows, list) and len(capture_rows) <= MAX_ROWS,
                    "PROVIDER_CAPTURE_ROW_BUDGET")
            fingerprints = set()
            for item in capture_rows:
                require(time.monotonic() < deadline, "AUDIT_TIME_BUDGET")
                require(isinstance(item, dict) and isinstance(item.get("legacy_row_sha256"), str)
                        and SHA.fullmatch(item["legacy_row_sha256"])
                        and all(isinstance(item.get(k), str) and item[k].strip()
                                for k in PROVENANCE), "PROVIDER_CAPTURE_ROW_INVALID")
                fingerprints.add((item["legacy_row_sha256"],
                                  *(item[k] for k in PROVENANCE)))
            capture["_fingerprints"] = fingerprints
            del capture["rows"]
            captures[relative] = capture
        elif kind == "sqlite":
            ref, count = scan_sqlite(path, known_ids, deadline)
            pin(ref, "sqlite_external")
            scanned["sqlite_external_rows"] += count
        else:
            ref, count = scan_json(path, known_ids, deadline,
                                   original_name=relative)
            pin(ref, kind)
            scanned[kind + "_files"] += count
    claims = manifest.get("provenance")
    require(isinstance(claims, list) and len(claims) <= MAX_ROWS,
            "PROVENANCE_ROW_BUDGET")
    claim_by_id = {}
    for claim in claims:
        require(isinstance(claim, dict) and type(claim.get("version_id")) is int,
                "PROVENANCE_ENTRY_INVALID")
        id_value = claim["version_id"]
        require(id_value not in claim_by_id, "PROVENANCE_DUPLICATE_ID")
        claim_by_id[id_value] = claim
    require(not (set(claim_by_id) - known_ids), "PROVENANCE_FOREIGN_ID")
    state = Counter()
    eligible: list[int] = []
    decisions: dict[int, str] = {}
    pit_states: dict[int, str] = {}
    for row in rows:
        day = date_iso(row["date"])
        pit_good = provenance_ok(row, claim_by_id.get(row["id"]), captures, at)
        pit_states[row["id"]] = "STRUCTURAL_MATCH_ONLY" if pit_good else "NO_VERIFICADO"
        if day is None or day > at.astimezone(ART).date():
            decision = "HOLD_BAD_OR_FUTURE_DATE"
        elif day >= cutoff:
            decision = "RETAIN_365D"
        elif row["id"] in pins:
            decision = "RETAIN_PINNED"
        elif not all_kinds:
            decision = "HOLD_MISSING_CROSS_STORE_COVERAGE"
        elif not pit_good:
            decision = "HOLD_PROVENANCE_PIT"
        else:
            decision = "COPY_SIMULATION_CANDIDATE"
            eligible.append(row["id"])
        decisions[row["id"]] = decision
        state[decision] += 1
    report = {
        "schema": SCHEMA, "state": "OFFHOST_CENSUS_COMPLETE_HOLD",
        "source_sha256": database_sha256, "manifest_sha256": manifest_sha256,
        "as_of_utc": at.isoformat(), "cutoff_inclusive_art": cutoff.isoformat(),
        "total_versions": len(rows), "decision_counts": dict(sorted(state.items())),
        "reference_pinned_versions": len(pins),
        "snapshot_kinds_present": sorted(kinds),
        "external_snapshot_kinds_complete_claimed": all_kinds,
        "scanned": dict(sorted(scanned.items())),
        "provenance_contract": "STRUCTURAL_ONLY_NOT_PROVIDER_AUTHENTICATED",
        "external_consumer_universe_verified": False,
        "host_purge_allowed": False, "physical_host_delete_count": 0,
        "physical_host_bytes_reclaimed": 0, "real_orders_sent_by_action": 0,
        "source_mutated": False,
    }
    if eligible and simulate:
        report["simulation"] = copy_simulation(
            source, eligible, root, database_sha256, deadline)
        report["state"] = "OFFHOST_COPY_SIMULATION_VERIFIED_HOLD"
    elif eligible:
        report["state"] = "OFFHOST_REHEARSAL_NOT_RUN_HOLD"
        report["simulation"] = {"reason": "SIMULATION_NOT_REQUESTED"}
    for original, stable, initial, expected in held:
        require(sha_file(stable, deadline) == expected,
                "SNAPSHOT_INODE_CHANGED_DURING_AUDIT")
        current = original.stat()
        require((current.st_dev, current.st_ino, current.st_size,
                 current.st_mtime_ns) == initial,
                "COPY_PATH_REPLACED_DURING_AUDIT")
        require(sha_file(original, deadline) == expected,
                "SNAPSHOT_PATH_HASH_CHANGED_DURING_AUDIT")
    if index_output is not None:
        report["per_row_index"] = write_index(
            root, index_output, rows, decisions, pin_sources, pit_states, deadline)
    return report


def audit(root: Path, database: str, database_sha256: str,
          manifest_path: str, manifest_sha256: str, as_of_utc: str,
          *, simulate: bool = True, budget_seconds: int = 240,
          index_output: str | None = None) -> dict:
    with ExitStack() as stack:
        return _audit(root, database, database_sha256, manifest_path,
                      manifest_sha256, as_of_utc, simulate=simulate,
                      budget_seconds=budget_seconds, index_output=index_output,
                      stack=stack, held=[])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner-root", type=Path, required=True)
    parser.add_argument("--database-copy", required=True)
    parser.add_argument("--database-sha256", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--as-of-utc", required=True)
    parser.add_argument("--no-simulate", action="store_true")
    parser.add_argument("--index-output", help="exclusive JSONL filename under runner root")
    args = parser.parse_args()
    try:
        result = audit(args.runner_root, args.database_copy,
                       args.database_sha256, args.manifest,
                       args.manifest_sha256, args.as_of_utc,
                       simulate=not args.no_simulate,
                       index_output=args.index_output)
        print(json.dumps(result, sort_keys=True))
    except (EvidenceError, sqlite3.DatabaseError, OSError) as exc:
        print(json.dumps({"schema": SCHEMA, "state": "NO_VERIFICADO",
                          "failure_class": str(exc) if isinstance(exc, EvidenceError)
                          else type(exc).__name__,
                          "host_purge_allowed": False,
                          "physical_host_delete_count": 0,
                          "real_orders_sent_by_action": 0}, sort_keys=True))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
