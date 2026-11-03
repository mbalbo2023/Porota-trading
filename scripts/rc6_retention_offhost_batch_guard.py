#!/usr/bin/env python3
"""A623 fail-closed OFFHOST reader for the 365-day retention batch review.

This closes the reader-contract gap in ``rc6_retention_batch_dryrun``: its
``read_batch`` callback *requires* a bounded regular file with exact bytes,
inode and SHA-256, but the pure planner does not implement filesystem checks.

The only output is a review receipt.  It has no DELETE, VACUUM, checkpoint,
SQLite, broker, network or host mutation code and never authorizes a purge.
An externally supplied digest authenticates bytes against that *claim*; it
does not independently establish the completeness of cross-store pins or the
origin of the capture.  Live observer WAL/SHM are expressly excluded.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any

from rc6_retention_batch_dryrun import review_batches
from rc6_source_retention_dryrun import _sha


SCHEMA = "RC6_RETENTION_OFFHOST_BATCH_GUARD_A623_V1"
CAPTURE_NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
BATCH_NAME = re.compile(r"batch-[0-9]{6}\.json\Z")
DOCUMENT_NAMES = frozenset({"index.json", "checkpoint.json"})
MAX_DIRECTORY_ENTRIES = 1024
MAX_INDEX_BYTES = 2 * 1024**2
MAX_BATCH_BYTES = 16 * 1024**2
MAX_CHECKPOINT_BYTES = 256 * 1024
CHUNK = 1024 * 1024
O_DIR = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
O_FILE = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)


def _duplicate_reject(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("RETENTION_COPY_JSON_DUPLICATE_KEY")
        result[name] = value
    return result


def _invalid_json_number(value: str):
    raise ValueError("RETENTION_COPY_JSON_NONINTEGER_NUMBER")


def _parse_object(raw: bytes) -> dict:
    try:
        data = json.loads(raw.decode("utf-8"),
                          object_pairs_hook=_duplicate_reject,
                          parse_constant=_invalid_json_number,
                          parse_float=_invalid_json_number)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("RETENTION_COPY_JSON_INVALID") from exc
    if not isinstance(data, dict):
        raise ValueError("RETENTION_COPY_JSON_OBJECT_REQUIRED")
    return data


def _identity(st: os.stat_result) -> tuple:
    return (st.st_dev, st.st_ino, st.st_mode, st.st_nlink,
            st.st_size, st.st_mtime_ns, st.st_ctime_ns)


class OffhostBatchReader:
    """Read one capture directory by pinned dirfds with no-follow checks.

    The capture is an already copied and hashed runner artifact, **never** a
    live path or database.  Direct callers provide an explicit root; only the
    CLI authorizes execution under the exact ``GITHUB_ACTIONS/RUNNER_TEMP``.
    """

    def __init__(self, runner_root: Path, capture_name: str):
        if (not isinstance(capture_name, str)
                or CAPTURE_NAME.fullmatch(capture_name) is None):
            raise ValueError("RETENTION_CAPTURE_DIRECTORY_NAME_INVALID")
        root = Path(runner_root).absolute()
        if (not root.is_dir() or root.is_symlink() or root.resolve() != root
                or str(root) in {"/", "/opt", "/opt/porota-trading"}):
            raise ValueError("RETENTION_RUNNER_SCOPE_INVALID")
        self.root = root
        self.capture_name = capture_name
        self._root_fd = None
        self._dir_fd = None
        self._root_identity = None
        self._dir_identity = None
        self.files_verified: list[dict] = []

    def __enter__(self):
        self._root_fd = os.open(self.root, O_DIR)
        try:
            self._dir_fd = os.open(self.capture_name, O_DIR, dir_fd=self._root_fd)
            self._root_identity = _identity(os.fstat(self._root_fd))
            self._dir_identity = _identity(os.fstat(self._dir_fd))
            self._check_scope()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self._dir_fd is not None:
            os.close(self._dir_fd)
            self._dir_fd = None
        if self._root_fd is not None:
            os.close(self._root_fd)
            self._root_fd = None

    def _check_scope(self):
        if self._dir_fd is None or self._root_fd is None:
            raise ValueError("RETENTION_CAPTURE_READER_NOT_OPEN")
        # Detect directory replacement, rename or root rebinding.  Reads use
        # pinned dirfds; path checks make even a replaced alias fail closed.
        try:
            root_now = os.stat(self.root, follow_symlinks=False)
            dir_now = os.stat(self.capture_name, dir_fd=self._root_fd,
                              follow_symlinks=False)
        except FileNotFoundError as exc:
            raise ValueError("RETENTION_CAPTURE_DIRECTORY_REPLACED") from exc
        if (not stat.S_ISDIR(root_now.st_mode)
                or not stat.S_ISDIR(dir_now.st_mode)
                or _identity(root_now) != self._root_identity
                or _identity(dir_now) != self._dir_identity
                or _identity(os.fstat(self._dir_fd)) != self._dir_identity):
            raise ValueError("RETENTION_CAPTURE_DIRECTORY_CHANGED")

    def _check_sidecars(self):
        self._check_scope()
        names = os.listdir(self._dir_fd)
        if len(names) > MAX_DIRECTORY_ENTRIES:
            raise ValueError("RETENTION_CAPTURE_DIRECTORY_BUDGET_EXCEEDED")
        if any(name.endswith(("-wal", "-shm", "-journal")) for name in names):
            raise ValueError("RETENTION_CAPTURE_LIVE_SQLITE_SIDECAR_PRESENT")

    def read_json(self, name: str, expected_sha256: str) -> dict:
        if name not in DOCUMENT_NAMES and (
                not isinstance(name, str) or BATCH_NAME.fullmatch(name) is None):
            raise ValueError("RETENTION_CAPTURE_DOCUMENT_NAME_INVALID")
        _sha(expected_sha256, code="RETENTION_CAPTURE_DOCUMENT_SHA_INVALID")
        self._check_sidecars()
        limit = (MAX_INDEX_BYTES if name == "index.json" else
                 MAX_CHECKPOINT_BYTES if name == "checkpoint.json" else MAX_BATCH_BYTES)
        try:
            path_before = os.stat(name, dir_fd=self._dir_fd,
                                  follow_symlinks=False)
            if (not stat.S_ISREG(path_before.st_mode)
                    or path_before.st_nlink != 1
                    or not 2 <= path_before.st_size <= limit):
                raise ValueError("RETENTION_CAPTURE_REGULAR_BOUNDED_COPY_REQUIRED")
            fd = os.open(name, O_FILE, dir_fd=self._dir_fd)
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError,
                OSError) as exc:
            raise ValueError("RETENTION_CAPTURE_DOCUMENT_OPEN_REJECTED") from exc
        try:
            start = os.fstat(fd)
            if _identity(start) != _identity(path_before):
                raise ValueError("RETENTION_CAPTURE_DOCUMENT_REPLACED")
            data = bytearray()
            h = hashlib.sha256()
            while True:
                chunk = os.read(fd, CHUNK)
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > limit:
                    raise ValueError("RETENTION_CAPTURE_DOCUMENT_BUDGET_EXCEEDED")
                h.update(chunk)
            if len(data) != start.st_size or h.hexdigest() != expected_sha256:
                raise ValueError("RETENTION_CAPTURE_DOCUMENT_SHA_MISMATCH")
            # Re-read the same pinned inode to detect a mid-scan race, even
            # if metadata is restored.  No second filesystem path is opened.
            os.lseek(fd, 0, os.SEEK_SET)
            second = hashlib.sha256()
            while True:
                chunk = os.read(fd, CHUNK)
                if not chunk:
                    break
                second.update(chunk)
            if second.hexdigest() != expected_sha256:
                raise ValueError("RETENTION_CAPTURE_DOCUMENT_RACE_DETECTED")
            after = os.fstat(fd)
            path_after = os.stat(name, dir_fd=self._dir_fd,
                                 follow_symlinks=False)
            if (_identity(after) != _identity(start)
                    or _identity(path_after) != _identity(start)):
                raise ValueError("RETENTION_CAPTURE_DOCUMENT_CHANGED")
            self._check_sidecars()
            result = _parse_object(bytes(data))
            self.files_verified.append({"name": name, "sha256": expected_sha256,
                                        "bytes": len(data)})
            return result
        finally:
            os.close(fd)


def guarded_review(reader: OffhostBatchReader, *, index_sha256: str,
                   as_of, checkpoint_sha256: str | None = None,
                   batch_limit: int = 8) -> dict:
    """Run A242 batches using a verified copy reader, without any write."""
    index = reader.read_json("index.json", index_sha256)
    checkpoint = (reader.read_json("checkpoint.json", checkpoint_sha256)
                  if checkpoint_sha256 is not None else None)
    review = review_batches(index, as_of=as_of, read_batch=reader.read_json,
                            checkpoint=checkpoint, batch_limit=batch_limit)
    reader._check_sidecars()
    # Fail-closed even if the caller later broadens the pure planner contract.
    if (review.get("host_purge_allowed") is not False
            or review.get("inventory_complete_proven") is not False
            or review.get("cross_store_pin_and_fk_verification") != "NO_VERIFICADO"
            or review.get("states", {}).get("UNPINNED_REVIEW_CANDIDATE_ONLY", 0)):
        raise ValueError("RETENTION_BATCH_REVIEW_UNSAFE_AUTHORITY")
    return {"schema": SCHEMA, "review": review,
            "capture_files": list(reader.files_verified),
            "capture_bytes_verified": sum(x["bytes"] for x in reader.files_verified),
            "capture_bytes_rehashed_twice": True,
            "copy_file_descriptors_nofollow": True,
            "copy_scope_and_sidecars_checked": True,
            "retention_calendar_days_including_today_art": 365,
            "capture_origin_independently_verified": False,
            "consumer_universe_independently_verified": False,
            "host_purge_allowed": False, "host_migration_allowed": False,
            "source_mutated": False, "offhost_files_mutated": False,
            "provider_calls": 0, "paper_entry_authority": False,
            "real_orders_sent": 0}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner-root", type=Path, required=True)
    parser.add_argument("--capture-dir", required=True)
    parser.add_argument("--index-sha256", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--checkpoint-sha256")
    parser.add_argument("--batch-limit", type=int, default=8)
    args = parser.parse_args()
    runner_temp = os.environ.get("RUNNER_TEMP")
    if (os.environ.get("GITHUB_ACTIONS") != "true" or not runner_temp
            or args.runner_root.absolute() != Path(runner_temp).absolute()):
        raise ValueError("RETENTION_GITHUB_ACTIONS_RUNNER_REQUIRED")
    with OffhostBatchReader(args.runner_root, args.capture_dir) as reader:
        receipt = guarded_review(reader, index_sha256=args.index_sha256,
                                 as_of=args.as_of,
                                 checkpoint_sha256=args.checkpoint_sha256,
                                 batch_limit=args.batch_limit)
    print(json.dumps(receipt, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
