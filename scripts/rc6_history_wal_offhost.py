#!/usr/bin/env python3
"""A511: transport a *quiescent* history MAIN+WAL to private runner storage.

No SQLite connection or temporary file is created on the source host.
The current host workflow must fail closed until independent writer-exclusion
evidence is available; a stable hash alone is not writer-exclusion proof.
The received copy is only an observed-history cohort, never global readiness.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import tarfile
import tempfile
import time
from typing import BinaryIO, Callable


SCHEMA = "rc6.a511.history-wal-offhost.v1"
SOURCE = Path("/opt/porota-trading/data/market_history.db")
MAX_TOTAL_BYTES = 1024**3
MAX_WAL_BYTES = 256 * 1024**2
FILES = {"": "history.sqlite", "-wal": "history.sqlite-wal"}
MANIFEST = "manifest.json"


class HistoryHold(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(value: bool, code: str) -> None:
    if not value:
        raise HistoryHold(code)


def _time(deadline: float) -> None:
    _require(time.monotonic() < deadline, "TIME_BUDGET_EXHAUSTED")


def _metadata(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _source_inventory(source: Path) -> dict[str, tuple[int, ...]]:
    _require(source.is_absolute() and source.resolve() == source,
             "SOURCE_PATH_UNSAFE")
    _require(not any(parent.is_symlink() for parent in source.parents),
             "SOURCE_PATH_UNSAFE")
    members = {}
    for suffix in ("", "-wal", "-shm", "-journal"):
        name = Path(str(source) + suffix)
        try:
            info = name.lstat()
        except FileNotFoundError:
            if suffix in FILES:
                raise HistoryHold("SOURCE_MEMBER_MISSING") from None
            continue
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                 "SOURCE_PATH_UNSAFE")
        members[suffix] = _metadata(info)
    _require("-journal" not in members, "ROLLBACK_JOURNAL_PRESENT")
    return members


def _open_pinned(source: Path, suffix: str, expected: tuple[int, ...]) -> int:
    for name in ("O_NOFOLLOW", "O_NOATIME", "O_CLOEXEC"):
        _require(hasattr(os, name), "OWNER_READ_CAPABILITY_REQUIRED")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_CLOEXEC
    try:
        descriptor = os.open(str(source) + suffix, flags)
    except OSError:
        raise HistoryHold("OWNER_READ_CAPABILITY_REQUIRED") from None
    if _metadata(os.fstat(descriptor)) != expected:
        os.close(descriptor)
        raise HistoryHold("SOURCE_CHANGED")
    return descriptor


def _digest_fd(descriptor: int, deadline: float) -> str:
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while True:
        _time(deadline)
        data = os.read(descriptor, 1024**2)
        if not data:
            return digest.hexdigest()
        digest.update(data)


class _Reader:
    def __init__(self, descriptor: int, deadline: float):
        self.descriptor = descriptor
        self.deadline = deadline
        self.digest = hashlib.sha256()

    def read(self, size: int) -> bytes:
        _time(self.deadline)
        data = os.read(self.descriptor, size)
        self.digest.update(data)
        return data


def capture_verified(
    source: Path, sink: BinaryIO, *, writer_proof: Callable[[], bool],
    time_budget_seconds: float = 125,
    max_total_bytes: int = MAX_TOTAL_BYTES,
    max_wal_bytes: int = MAX_WAL_BYTES,
) -> dict:
    """Write tar stream only when a separate host writer witness says quiet.

    A positive synthetic witness is test-only. The real CLI uses the guarded
    host probe, which currently returns HOLD when ownership is incomplete.
    """
    deadline = time.monotonic() + time_budget_seconds
    source = Path(source)
    before = _source_inventory(source)
    _require(before[""][4] >= 4096 and before["-wal"][4] > 0,
             "SOURCE_MEMBER_INVALID")
    _require(0 < max_total_bytes <= MAX_TOTAL_BYTES and
             0 < max_wal_bytes <= MAX_WAL_BYTES and
             before["-wal"][4] <= max_wal_bytes and
             before[""][4] + before["-wal"][4] <= max_total_bytes,
             "SOURCE_BYTE_LIMIT")
    _require(writer_proof() is True, "WRITER_COVERAGE_UNVERIFIED")
    descriptors = {}
    try:
        for suffix in FILES:
            descriptors[suffix] = _open_pinned(source, suffix, before[suffix])
        baseline = {s: _digest_fd(fd, deadline)
                    for s, fd in descriptors.items()}
        _require(_source_inventory(source) == before, "SOURCE_CHANGED")
        with tarfile.open(fileobj=sink, mode="w|", format=tarfile.PAX_FORMAT) as archive:
            streamed = {}
            for suffix, member in FILES.items():
                fd = descriptors[suffix]
                os.lseek(fd, 0, os.SEEK_SET)
                item = tarfile.TarInfo(member)
                item.size = before[suffix][4]
                item.mode = 0o600
                item.mtime = 0
                reader = _Reader(fd, deadline)
                archive.addfile(item, reader)
                streamed[suffix] = reader.digest.hexdigest()
            verified = {s: _digest_fd(fd, deadline)
                        for s, fd in descriptors.items()}
            _require(streamed == baseline == verified, "SOURCE_CHANGED")
            _require(_source_inventory(source) == before,
                     "SOURCE_CHANGED")
            _require(writer_proof() is True, "WRITER_COVERAGE_UNVERIFIED")
            manifest = {
                "schema": SCHEMA, "writer_exclusion": "BOUNDED_WITNESS",
                "source_parts": [
                    {"name": FILES[s], "size": before[s][4], "sha256": streamed[s]}
                    for s in FILES
                ],
                "shm_observed": "-shm" in before,
                "cohort_scope": "HISTORY_VERSION_ROWS_ONLY",
                "global_universe_coverage": "NO_VERIFICADO",
                "readiness_implication": "NONE",
            }
            payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
            item = tarfile.TarInfo(MANIFEST)
            item.size = len(payload)
            item.mode = 0o600
            item.mtime = 0
            archive.addfile(item, io.BytesIO(payload))
        return {"schema": SCHEMA, "state": "VERIFIED_STREAM",
                "main_bytes": before[""][4], "wal_bytes": before["-wal"][4],
                "writer_exclusion": "BOUNDED_WITNESS",
                "global_universe_coverage": "NO_VERIFICADO",
                "readiness_implication": "NONE"}
    except OSError:
        raise HistoryHold("SOURCE_UNAVAILABLE") from None
    finally:
        for fd in descriptors.values():
            os.close(fd)


def verify_private_bundle(bundle: BinaryIO, private_root: Path, *,
                          time_budget_seconds: float = 180,
                          max_total_bytes: int = MAX_TOTAL_BYTES) -> dict:
    """Validate and read SQLite only on a temporary, runner-owned copy."""
    deadline = time.monotonic() + time_budget_seconds
    root = Path(private_root)
    info = root.lstat()
    _require(stat.S_ISDIR(info.st_mode) and not root.is_symlink() and
             info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700,
             "RUNNER_PRIVATE_DIRECTORY_REQUIRED")
    # The only supported producer is A511's uncompressed, record-padded TAR.
    # Size must be inspected independently of tarfile, which silently ignores
    # covert trailing bytes (and may read them into its private buffer).
    _require(bundle.seekable(), "BUNDLE_MEMBER_UNSAFE")
    bundle_start = bundle.tell()
    bundle.seek(0, os.SEEK_END)
    bundle_size = bundle.tell() - bundle_start
    bundle.seek(bundle_start)
    with tempfile.TemporaryDirectory(prefix="rc6-a511-", dir=root) as temporary:
        directory = Path(temporary)
        found = {}
        total = 0
        try:
            with tarfile.open(fileobj=bundle, mode="r|") as archive:
                for member in archive:
                    _time(deadline)
                    _require(member.isfile() and member.name in (*FILES.values(), MANIFEST) and
                             member.name not in found, "BUNDLE_MEMBER_UNSAFE")
                    total += member.size
                    _require(total <= max_total_bytes + 16384,
                             "BUNDLE_BYTE_LIMIT")
                    _require(member.size >= 0, "BUNDLE_MEMBER_UNSAFE")
                    source = archive.extractfile(member)
                    _require(source is not None, "BUNDLE_MEMBER_UNSAFE")
                    destination = directory / member.name
                    with destination.open("xb") as stream:
                        os.chmod(destination, 0o600)
                        digest = hashlib.sha256()
                        remaining = member.size
                        while remaining:
                            _time(deadline)
                            piece = source.read(min(1024**2, remaining))
                            _require(bool(piece), "BUNDLE_TRUNCATED")
                            stream.write(piece)
                            digest.update(piece)
                            remaining -= len(piece)
                    found[member.name] = (member.size, digest.hexdigest())
            # A511 writes exactly these three short-name members with zero
            # metadata, two end blocks and 10 KiB TAR record padding.
            # Reject compressed, PAX-extra, suffix or other noncanonical data.
            member_bytes = sum(
                tarfile.BLOCKSIZE +
                ((part_size + tarfile.BLOCKSIZE - 1) // tarfile.BLOCKSIZE) *
                tarfile.BLOCKSIZE
                for part_size, _ in found.values()
            )
            expected_bytes = (
                (member_bytes + 2 * tarfile.BLOCKSIZE +
                 tarfile.RECORDSIZE - 1) // tarfile.RECORDSIZE
            ) * tarfile.RECORDSIZE
            _require(bundle_size == expected_bytes, "BUNDLE_MEMBER_UNSAFE")
            bundle.seek(bundle_start + member_bytes)
            remaining_padding = bundle_size - member_bytes
            while remaining_padding:
                _time(deadline)
                padding = bundle.read(min(8192, remaining_padding))
                _require(bool(padding) and not any(padding),
                         "BUNDLE_MEMBER_UNSAFE")
                remaining_padding -= len(padding)
            _require(set(found) == {*FILES.values(), MANIFEST},
                     "BUNDLE_MEMBER_MISSING")
            manifest_path = directory / MANIFEST
            _require(found[MANIFEST][0] <= 8192, "BUNDLE_MANIFEST_INVALID")
            manifest = json.loads(manifest_path.read_text())
            _require(isinstance(manifest, dict) and manifest.get("schema") == SCHEMA and
                     manifest.get("writer_exclusion") == "BOUNDED_WITNESS" and
                     type(manifest.get("shm_observed")) is bool and
                     manifest.get("cohort_scope") == "HISTORY_VERSION_ROWS_ONLY" and
                     manifest.get("global_universe_coverage") == "NO_VERIFICADO" and
                     manifest.get("readiness_implication") == "NONE",
                     "BUNDLE_MANIFEST_INVALID")
            parts = manifest.get("source_parts")
            _require(isinstance(parts, list) and len(parts) == 2 and
                     {p.get("name") for p in parts if isinstance(p, dict)} ==
                     set(FILES.values()), "BUNDLE_MANIFEST_INVALID")
            _require(all(type(p.get("size")) is int and
                         isinstance(p.get("sha256"), str) and
                         re.fullmatch(r"[0-9a-f]{64}", p["sha256"])
                         for p in parts), "BUNDLE_MANIFEST_INVALID")
            by_name = {p["name"]: p for p in parts}
            main_size = by_name[FILES[""]]["size"]
            wal_size = by_name[FILES["-wal"]]["size"]
            _require(main_size >= 4096 and 0 < wal_size <= MAX_WAL_BYTES and
                     main_size + wal_size <= max_total_bytes,
                     "BUNDLE_MANIFEST_INVALID")
            for part in parts:
                _require((part["size"], part["sha256"]) == found[part["name"]],
                         "BUNDLE_HASH_MISMATCH")
            db_path = directory / FILES[""]
            connection = sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True,
                                         timeout=0.025)
            try:
                connection.execute("PRAGMA query_only=ON")
                connection.execute("PRAGMA trusted_schema=OFF")
                connection.set_progress_handler(
                    lambda: int(time.monotonic() >= deadline), 100)
                _require(connection.execute("PRAGMA quick_check").fetchone()[0] == "ok",
                         "BUNDLE_SQLITE_INVALID")
                row_count = connection.execute(
                    "SELECT COUNT(*) FROM history_versions_v2").fetchone()[0]
                # history_versions_v2 has no authoritative currency column in
                # the existing eight-column contract. Its rows are NOT a
                # signed PPI 5-key cohort or a denominator of zero-row assets.
                _require(0 <= row_count <= 1_000_000,
                         "OBSERVED_ROW_BUDGET")
            finally:
                connection.close()
            return {
                "schema": SCHEMA, "state": "OBSERVED_HISTORY_ONLY",
                "observed_fivekey_count": "NO_VERIFICADO",
                "fivekey_count_verified": False,
                "history_rows": row_count,
                "cohort_scope": "HISTORY_VERSION_ROWS_ONLY",
                "global_universe_coverage": "NO_VERIFICADO",
                "full_ohlc_certified": False,
                "readiness_implication": "NONE",
                "source_authority": "COPY_ONLY",
                "provider_requests": 0, "host_mutations": 0,
            }
        except (OSError, EOFError, tarfile.TarError, sqlite3.Error,
                ValueError, KeyError, TypeError):
            raise HistoryHold("BUNDLE_UNVERIFIED") from None


HOST_FAILURE_CODES = frozenset({
    "WRITER_COVERAGE_UNVERIFIED", "SOURCE_PATH_UNSAFE",
    "SOURCE_MEMBER_MISSING", "SOURCE_MEMBER_INVALID", "SOURCE_BYTE_LIMIT",
    "SOURCE_CHANGED", "SOURCE_UNAVAILABLE", "ROLLBACK_JOURNAL_PRESENT",
    "OWNER_READ_CAPABILITY_REQUIRED", "TIME_BUDGET_EXHAUSTED",
})
PRIVATE_FAILURE = re.compile(rb"A511_REJECTED=([A-Z_]+)\n?\Z")


def classify_host_failure(returncode: int, private_stderr: bytes) -> str:
    """Only one exact typed failure may leave private runner stderr."""
    if returncode != 2 or not isinstance(private_stderr, bytes) or len(private_stderr) > 160:
        return "REMOTE_FAILURE_UNVERIFIED"
    match = PRIVATE_FAILURE.fullmatch(private_stderr)
    if match is None:
        return "REMOTE_FAILURE_UNVERIFIED"
    code = match.group(1).decode("ascii")
    return ("REMOTE_GUARD_" + code if code in HOST_FAILURE_CODES
            else "REMOTE_FAILURE_UNVERIFIED")


def _host_writer_witness() -> bool:
    # Existing named-unit inactivity and snapshots do not prove no other
    # process holds a live writer FD. No operator-controlled bypass flag.
    # Do not run the physical copy until an independently reviewed host-wide
    # witness exists and this guard is implemented with adversarial tests.
    return False


def main(argv=None, *, source: Path = SOURCE,
         stdout: BinaryIO | None = None, stderr=None) -> int:
    parser = argparse.ArgumentParser(description="RC6 private offhost WAL capture")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--host-stream", action="store_true")
    modes.add_argument("--runner-verify", action="store_true")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--private-root", type=Path)
    args = parser.parse_args(argv)
    stream = stdout if stdout is not None else sys.stdout.buffer
    errors = stderr if stderr is not None else sys.stderr
    try:
        if args.host_stream:
            _require(args.bundle is None and args.private_root is None,
                     "SOURCE_PATH_UNSAFE")
            capture_verified(source, stream, writer_proof=_host_writer_witness)
        else:
            _require(args.bundle is not None and args.private_root is not None,
                     "RUNNER_PRIVATE_DIRECTORY_REQUIRED")
            with args.bundle.open("rb") as bundle:
                receipt = verify_private_bundle(bundle, args.private_root)
            stream.write(json.dumps(receipt, sort_keys=True,
                                    separators=(",", ":")).encode() + b"\n")
        return 0
    except HistoryHold as exc:
        errors.write("A511_REJECTED=" + exc.code + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

