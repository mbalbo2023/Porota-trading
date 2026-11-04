#!/usr/bin/env python3
"""A665: bounded, SHA-labelled READ_ONLY cold-audit queue/receipt counts.

This intentionally DOES NOT import the runtime, call ensure_idle/resolve, open
SQLite, issue provider requests, or expose receipt names/contents.  On the host
the Action first makes /opt/porota-trading/data read-only in a private mount
namespace; this script verifies that mount independently on each opened fd.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
from zoneinfo import ZoneInfo


SCHEMA = "rc6.a665.cold-queue-readonly.v1"
DATA = Path("/opt/porota-trading/data")
COLD_PARTS = ("paper_v17", "artifacts", "observer_v17.db", "dynamic-shadow", "cold-audit")
MAX_ENTRIES = 512
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
DIRECTORY_FLAGS = FLAGS | os.O_DIRECTORY
NAME = re.compile(r"(?:queue\.json|(?:receipt|manifest)-[a-f0-9]{32}\.json|"
                  r"[a-f0-9]{32}\.pack|\.capture-[a-f0-9]{32}\.tmp|\.write-[a-f0-9]{32}\.tmp)")
RECEIPT = re.compile(r"receipt-([a-f0-9]{32})\.json")
SHA = re.compile(r"[a-f0-9]{40}")
HEX64 = re.compile(r"[a-f0-9]{64}")
HEX32 = re.compile(r"[a-f0-9]{32}")
QUEUE_STATES = frozenset(("FAILED", "PENDING", "COMPLETE"))
RECEIPT_STATES = frozenset(("FAILED", "COMPLETE", "ABORTED_UNPUBLISHED"))


class Hold(Exception):
    """Only a static non-sensitive category reaches the reporting layer."""


def _check_directory(fd: int, *, readonly: bool, private: bool = False):
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode):
        raise Hold("DIRECTORY_HOLD")
    if private and stat.S_IMODE(info.st_mode) & 0o077:
        raise Hold("PRIVATE_DIRECTORY_HOLD")
    if readonly and not os.fstatvfs(fd).f_flag & os.ST_RDONLY:
        raise Hold("READONLY_MOUNT_HOLD")
    return info


def _open_tree(data: Path, parts: tuple[str, ...], *, readonly: bool):
    """Walk no-follow directory fds; never resolve a symlink in data."""
    fds = []
    try:
        fd = os.open(data, DIRECTORY_FLAGS)
        fds.append(fd)
        _check_directory(fd, readonly=readonly)
        for component in parts:
            fd = os.open(component, DIRECTORY_FLAGS, dir_fd=fd)
            fds.append(fd)
            _check_directory(fd, readonly=readonly,
                             private=(component == "cold-audit"))
        return fds
    except BaseException:
        for fd in reversed(fds):
            os.close(fd)
        raise


def _read_file(fd: int, name: str, *, private: bool, readonly: bool,
               owner_uid: int | None = None, absent_ok: bool = False):
    try:
        handle = os.open(name, FLAGS, dir_fd=fd)
    except FileNotFoundError:
        if absent_ok:
            return None
        raise Hold("RECEIPT_DISAPPEARED_HOLD") from None
    try:
        before = os.fstat(handle)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                (private and stat.S_IMODE(before.st_mode) & 0o077) or
                (owner_uid is not None and before.st_uid != owner_uid) or
                not 2 <= before.st_size <= MAX_FILE_BYTES):
            raise Hold("PRIVATE_FILE_IDENTITY_HOLD")
        if readonly and not os.fstatvfs(handle).f_flag & os.ST_RDONLY:
            raise Hold("READONLY_MOUNT_HOLD")
        data = os.read(handle, MAX_FILE_BYTES + 1)
        after = os.fstat(handle)
        if (len(data) != before.st_size or
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                 before.st_ctime_ns) !=
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
                 after.st_ctime_ns)):
            raise Hold("RACE_OR_BOUNDED_READ_HOLD")
        return data
    finally:
        os.close(handle)


def _payload(raw: bytes):
    try:
        envelope = json.loads(raw)
        if (type(envelope) is not dict or set(envelope) != {"payload", "sha256"} or
                type(envelope["payload"]) is not dict or
                not isinstance(envelope["sha256"], str) or
                not HEX64.fullmatch(envelope["sha256"])):
            raise ValueError("ENVELOPE_INVALID")
        canonical = json.dumps(envelope["payload"], sort_keys=True,
                               separators=(",", ":"), allow_nan=False,
                               default=str).encode()
        if not hmac.compare_digest(hashlib.sha256(canonical).hexdigest(),
                                   envelope["sha256"]):
            raise ValueError("ENVELOPE_DIGEST")
        return envelope["payload"]
    except (ValueError, TypeError, UnicodeError, OverflowError):
        raise Hold("COLD_ENVELOPE_HASH_HOLD") from None


def _provenance(data: Path, *, readonly: bool):
    fds = _open_tree(data, ("deploy",), readonly=readonly)
    try:
        raw = _read_file(fds[-1], "CURRENT_STATE_V2.json",
                         private=False, readonly=readonly)
        try:
            record = json.loads(raw)
            sha = record.get("deploy_sha") if isinstance(record, dict) else None
        except (ValueError, UnicodeError, TypeError):
            sha = None
        if not isinstance(sha, str) or not SHA.fullmatch(sha):
            raise Hold("DEPLOY_SHA_PROVENANCE_HOLD")
        return sha
    finally:
        for fd in reversed(fds):
            os.close(fd)


def _counts(data: Path, *, readonly: bool):
    fds = _open_tree(data, COLD_PARTS, readonly=readonly)
    try:
        fd = fds[-1]
        initial = os.fstat(fd)
        private_uid = initial.st_uid
        names = os.listdir(fd)
        if len(names) > MAX_ENTRIES:
            raise Hold("FILE_COUNT_BOUND_HOLD")
        if any(not NAME.fullmatch(name) for name in names):
            raise Hold("PRIVATE_BASENAME_HOLD")
        receipts = sorted(name for name in names if RECEIPT.fullmatch(name))
        failed = []
        observed_bytes = 0
        for name in receipts:
            raw = _read_file(fd, name, private=True, readonly=readonly,
                             owner_uid=private_uid)
            observed_bytes += len(raw)
            if observed_bytes > MAX_TOTAL_BYTES:
                raise Hold("TOTAL_BYTES_BOUND_HOLD")
            receipt = _payload(raw)
            state = receipt.get("state")
            if state not in RECEIPT_STATES or not isinstance(state, str):
                raise Hold("RECEIPT_STATE_HOLD")
            if state == "FAILED":
                ident = receipt.get("snapshot_id")
                manifest_sha = receipt.get("manifest_sha256")
                if (not isinstance(ident, str) or not HEX32.fullmatch(ident) or
                        ident != RECEIPT.fullmatch(name).group(1) or
                        not isinstance(manifest_sha, str) or
                        not HEX64.fullmatch(manifest_sha)):
                    raise Hold("FAILED_RECEIPT_BINDING_HOLD")
                failed.append((ident, manifest_sha))  # Never included in output.
        queue_raw = _read_file(fd, "queue.json", private=True, readonly=readonly,
                               owner_uid=private_uid, absent_ok=True)
        if queue_raw is not None:
            observed_bytes += len(queue_raw)
            if observed_bytes > MAX_TOTAL_BYTES:
                raise Hold("TOTAL_BYTES_BOUND_HOLD")
            queue = _payload(queue_raw)
            queue_state = queue.get("state")
            if not isinstance(queue_state, str) or queue_state not in QUEUE_STATES:
                raise Hold("QUEUE_STATE_HOLD")
        else:
            queue, queue_state = None, "ABSENT"
        if len(failed) > 1:
            binding = "MULTIPLE_FAILED_RECEIPTS"
        elif len(failed) == 1:
            first = failed[0]
            binding = ("MATCHES_FAILED_RECEIPT" if queue_state == "FAILED" and
                       (queue.get("snapshot_id"), queue.get("manifest_sha256")) == first
                       else "FAILED_RECEIPT_QUEUE_NOT_BOUND")
        elif queue_state == "FAILED":
            binding = "FAILED_QUEUE_WITHOUT_FAILED_RECEIPT"
        else:
            binding = "NO_FAILED_RECEIPT"
        final = os.fstat(fd)
        if (sorted(os.listdir(fd)) != sorted(names) or
                (initial.st_dev, initial.st_ino, initial.st_mtime_ns, initial.st_ctime_ns) !=
                (final.st_dev, final.st_ino, final.st_mtime_ns, final.st_ctime_ns)):
            raise Hold("DIRECTORY_RACE_HOLD")
        return {"receipt_count": len(receipts),
                "failed_receipt_count": len(failed),
                "queue_state": queue_state, "queue_failed_binding": binding,
                "folder_entries_checked": len(names),
                "envelopes_hash_verified": True,
                "metadata_snapshot_stable": True}
    finally:
        for fd in reversed(fds):
            os.close(fd)


def inspect(data: Path = DATA, *, readonly: bool = True):
    now = datetime.now(timezone.utc)
    result = {
        "schema": SCHEMA, "generated_at_utc": now.isoformat(),
        "generated_at_art": now.astimezone(ZoneInfo("America/Argentina/Buenos_Aires")).isoformat(),
        "read_only": True, "provider_gets": 0, "orders_called": False,
        "mutations": 0, "cold_audit_runtime_called": False,
        "state": "NO_VERIFICADO", "reason": "HOST_READ_NOT_VERIFIED",
        "os_readonly_mount_verified": False,
        "observed_deploy_sha": None, "receipt_count": None,
        "failed_receipt_count": None, "folder_entries_checked": None,
        "queue_state": "NO_VERIFICADO", "queue_failed_binding": "NO_VERIFICADO",
        "envelopes_hash_verified": False, "metadata_snapshot_stable": False,
        "paper_admission_verified": False, "causal_fix_verified": False,
    }
    try:
        observed = _provenance(data, readonly=readonly)
        counts = _counts(data, readonly=readonly)
        result.update(counts)
        result.update(state="READ_ONLY_OBSERVED", reason="COUNTS_ONLY_NO_REPAIR",
                      os_readonly_mount_verified=readonly,
                      observed_deploy_sha=observed)
    except Hold as exc:
        result["reason"] = str(exc) if str(exc) in {
            "DIRECTORY_HOLD", "PRIVATE_DIRECTORY_HOLD", "READONLY_MOUNT_HOLD",
            "RECEIPT_DISAPPEARED_HOLD", "PRIVATE_FILE_IDENTITY_HOLD",
            "RACE_OR_BOUNDED_READ_HOLD", "COLD_ENVELOPE_HASH_HOLD",
            "DEPLOY_SHA_PROVENANCE_HOLD", "FILE_COUNT_BOUND_HOLD",
            "PRIVATE_BASENAME_HOLD", "TOTAL_BYTES_BOUND_HOLD",
            "RECEIPT_STATE_HOLD", "FAILED_RECEIPT_BINDING_HOLD",
            "QUEUE_STATE_HOLD", "DIRECTORY_RACE_HOLD"} else "HOST_READ_NOT_VERIFIED"
    except FileNotFoundError:
        result["reason"] = "ROOT_OR_DEPLOY_ABSENT"
    except (OSError, ValueError, TypeError):
        result["reason"] = "HOST_READ_NOT_VERIFIED"
    return result


if __name__ == "__main__":
    print(json.dumps(inspect(), sort_keys=True, separators=(",", ":"), allow_nan=False))
