#!/usr/bin/env python3
"""A689: irreversible, durable, per-BYMA-ART-day and slot admission claim.

This guard is deliberately independent of the legacy PAPER database, PPI Watch
and the shared PPI budget.  A committed claim is never automatically removed:
an interrupted or ambiguous attempt therefore fails closed on retry.

The module is streamed from a pinned GitHub checkout into the PAPER container
by the A670 workflow.  It must not rely on the Python module being installed
in the current immutable product image.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import os
from pathlib import Path
import re
import sqlite3
import stat
from zoneinfo import ZoneInfo


ART = ZoneInfo("America/Argentina/Buenos_Aires")
SLOT_MINUTES = {"1045": 10 * 60 + 45, "1120": 11 * 60 + 20,
                "1430": 14 * 60 + 30, "1630": 16 * 60 + 30}
CLAIM_SUBDIR = "a670-slot-claims"
CLAIM_FILE = "claims.sqlite"
REPOSITORY = "mbalbo2023/Porota-trading"
CONTROL_REF = "refs/heads/main"
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
SCHEMA_DDL = ("CREATE TABLE IF NOT EXISTS slot_claims ("
              "art_date TEXT NOT NULL, slot TEXT NOT NULL, "
              "run_id TEXT NOT NULL, control_sha TEXT NOT NULL, "
              "claimed_at_utc TEXT NOT NULL, "
              "PRIMARY KEY (art_date, slot)) WITHOUT ROWID")


class SlotClaimHold(Exception):
    """Conservative rejection; callers MUST execute zero provider GETs."""


def _require(condition, error):
    if not condition:
        raise SlotClaimHold(error)


def _private_dir_stat(path, *, require_mount=False):
    _require(not path.is_symlink(), "SYMLINK_DIRECTORY")
    try:
        current = os.lstat(path)
    except OSError as exc:
        raise SlotClaimHold("DIRECTORY_UNAVAILABLE") from exc
    _require(stat.S_ISDIR(current.st_mode), "NOT_DIRECTORY")
    _require(current.st_uid == os.geteuid(), "DIRECTORY_WRONG_UID")
    _require(not current.st_mode & 0o077, "DIRECTORY_NOT_PRIVATE")
    if require_mount:
        _require(os.path.ismount(path), "POROTA_MOUNT_NOT_PROVEN")
    return current


def _validate_metadata(*, art_date, slot, run_id, run_attempt,
                       control_sha, repository, control_ref, event_name,
                       clock):
    _require(isinstance(art_date, str) and
             re.fullmatch(r"\d{4}-\d{2}-\d{2}", art_date), "ART_DATE_INVALID")
    try:
        parsed = date.fromisoformat(art_date)
    except ValueError as exc:
        raise SlotClaimHold("ART_DATE_INVALID") from exc
    _require(parsed.isoformat() == art_date, "ART_DATE_INVALID")
    _require(slot in SLOT_MINUTES, "SLOT_INVALID")
    _require(isinstance(run_id, str) and run_id.isascii() and run_id.isdecimal()
             and 1 <= len(run_id) <= 20 and int(run_id) > 0, "RUN_ID_INVALID")
    _require(str(run_attempt) == "1", "RETRY_ATTEMPT_HOLD")
    _require(isinstance(control_sha, str) and HEX40.fullmatch(control_sha),
             "CONTROL_SHA_INVALID")
    _require(repository == REPOSITORY, "REPOSITORY_NOT_ATTESTED")
    _require(control_ref == CONTROL_REF, "REF_NOT_ATTESTED")
    _require(event_name in ("schedule", "workflow_dispatch"), "EVENT_NOT_ALLOWED")
    _require(isinstance(clock, datetime) and clock.tzinfo is not None,
             "CLOCK_INVALID")
    local = clock.astimezone(ART)
    _require(local.date() == parsed, "ART_DATE_CLOCK_MISMATCH")
    minutes = 60 * local.hour + local.minute
    # Match the host's A669 guard: no early admission, at most 60m late.
    _require(0 <= minutes - SLOT_MINUTES[slot] <= 60, "SLOT_WINDOW_HOLD")


def claim_once(*, art_date, slot, run_id, run_attempt, control_sha,
               repository, control_ref, event_name, clock=None,
               data_root=Path("/app/data"), require_mount=True):
    """Consume exactly one UNIQUE(ART date,slot), or raise SlotClaimHold.

    The production caller does not set data_root/require_mount.  An OFFHOST
    test may inject a private temporary directory with require_mount=False.
    SQLite serializes independent processes using BEGIN IMMEDIATE and protects
    against concurrent claims with its PRIMARY KEY uniqueness constraint.
    """
    if clock is None:
        clock = datetime.now(timezone.utc)
    _validate_metadata(art_date=art_date, slot=slot, run_id=run_id,
                       run_attempt=run_attempt, control_sha=control_sha,
                       repository=repository, control_ref=control_ref,
                       event_name=event_name, clock=clock)
    _require(isinstance(data_root, Path), "DATA_ROOT_INVALID")
    # A runtime caller can never point the claim into a legacy/PPI Watch DB.
    if require_mount:
        _require(data_root == Path("/app/data"), "NONCANONICAL_DATA_ROOT")
    try:
        _require(not data_root.is_symlink(), "MOUNT_SYMLINK")
        root_stat = os.lstat(data_root)
        _require(stat.S_ISDIR(root_stat.st_mode), "DATA_ROOT_NOT_DIRECTORY")
        _require(root_stat.st_uid == os.geteuid(), "DATA_ROOT_WRONG_UID")
        _require(os.path.ismount(data_root) if require_mount else True,
                 "POROTA_MOUNT_NOT_PROVEN")
        # Group write on the parent permits another process in the same group
        # to rename/delete a 0700 child and lose claims. Reading is okay;
        # group/world write is not (unless a future attestable layout exists).
        _require(not root_stat.st_mode & 0o022,
                 "DATA_ROOT_GROUP_OR_WORLD_WRITABLE")
        folder = data_root / CLAIM_SUBDIR
        parent_flags = (os.O_RDONLY | os.O_DIRECTORY |
                        getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
        parent_fd = os.open(data_root, parent_flags)
        try:
            parent_stat = os.fstat(parent_fd)
            _require((parent_stat.st_dev, parent_stat.st_ino) ==
                     (root_stat.st_dev, root_stat.st_ino), "DATA_ROOT_REPLACED")
            try:
                os.mkdir(CLAIM_SUBDIR, 0o700, dir_fd=parent_fd)
                newly_created = True
            except FileExistsError:
                newly_created = False
            # A new directory name is an entry in the PARENT.  Fsyncing only
            # the child loses that name on power loss even if SQLite COMMIT
            # was synchronous, which could permit a second GET after reboot.
            # Fsync parent also when preexisting to attest the name now.
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        _private_dir_stat(folder)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        directory_fd = os.open(folder, flags | os.O_DIRECTORY)
        try:
            folder_stat = os.fstat(directory_fd)
            _require(folder_stat.st_dev == root_stat.st_dev,
                     "CLAIM_DIR_DIFFERENT_DEVICE")
            _require(folder_stat.st_ino == os.lstat(folder).st_ino,
                     "CLAIM_DIR_REPLACED")
            file_flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            file_flags |= getattr(os, "O_CLOEXEC", 0)
            db_fd = os.open(CLAIM_FILE, file_flags, 0o600, dir_fd=directory_fd)
            try:
                file_stat = os.fstat(db_fd)
                _require(stat.S_ISREG(file_stat.st_mode), "CLAIM_NOT_REGULAR_FILE")
                _require(file_stat.st_uid == os.geteuid(), "CLAIM_WRONG_UID")
                _require(file_stat.st_nlink == 1, "CLAIM_HARDLINK_HOLD")
                _require(not file_stat.st_mode & 0o077, "CLAIM_NOT_PRIVATE")
                _require(file_stat.st_dev == folder_stat.st_dev,
                         "CLAIM_DB_DIFFERENT_DEVICE")
                db_path = folder / CLAIM_FILE
                _require(os.lstat(db_path).st_ino == file_stat.st_ino,
                         "CLAIM_DB_REPLACED")
                if newly_created:
                    os.fsync(directory_fd)
                con = sqlite3.connect(str(db_path), timeout=2.0,
                                      isolation_level=None)
                try:
                    _require(os.stat(db_path, follow_symlinks=False).st_ino ==
                             file_stat.st_ino, "CLAIM_DB_REPLACED")
                    con.execute("PRAGMA journal_mode=DELETE")
                    con.execute("PRAGMA synchronous=FULL")
                    con.execute("PRAGMA busy_timeout=2000")
                    con.execute("BEGIN IMMEDIATE")
                    con.execute(SCHEMA_DDL)
                    # Reject rogue triggers/views/indexes/extra tables. A
                    # BEFORE INSERT RAISE(IGNORE) trigger can make SQLite
                    # return success without actually persisting a claim.
                    actual_schema = con.execute(
                        "SELECT type,name,sql FROM sqlite_master ORDER BY name").fetchall()
                    _require(actual_schema == [(
                        "table", "slot_claims",
                        SCHEMA_DDL.replace("IF NOT EXISTS ", ""))],
                             "CLAIM_SCHEMA_INVALID")
                    claimed_at = clock.astimezone(timezone.utc).isoformat()
                    inserted = con.execute(
                        "INSERT INTO slot_claims "
                        "(art_date,slot,run_id,control_sha,claimed_at_utc) "
                        "VALUES (?,?,?,?,?)",
                        (art_date, slot, run_id, control_sha, claimed_at))
                    _require(inserted.rowcount == 1, "CLAIM_INSERT_UNCONFIRMED")
                    expected = (art_date, slot, run_id, control_sha, claimed_at)
                    selected = con.execute(
                        "SELECT art_date,slot,run_id,control_sha,claimed_at_utc "
                        "FROM slot_claims WHERE art_date=? AND slot=?",
                        (art_date,slot)).fetchone()
                    _require(selected == expected, "CLAIM_INSERT_UNCONFIRMED")
                    con.commit()
                    selected = con.execute(
                        "SELECT art_date,slot,run_id,control_sha,claimed_at_utc "
                        "FROM slot_claims WHERE art_date=? AND slot=?",
                        (art_date,slot)).fetchone()
                    _require(selected == expected, "CLAIM_COMMIT_UNCONFIRMED")
                    # SQLite FULL sync + fsync directory retain the claim even
                    # across process crashes after commit and before PPI GET.
                    os.fsync(db_fd)
                    os.fsync(directory_fd)
                except BaseException:
                    try:
                        con.rollback()
                    finally:
                        raise
                finally:
                    con.close()
                return {"art_date": art_date, "slot": slot,
                        "run_id": run_id, "state": "CLAIMED_DURABLE"}
            finally:
                os.close(db_fd)
        finally:
            os.close(directory_fd)
    except SlotClaimHold:
        raise
    except sqlite3.IntegrityError as exc:
        raise SlotClaimHold("ALREADY_CLAIMED") from exc
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        raise SlotClaimHold("CLAIM_STORAGE_OR_LOCK_HOLD") from exc
