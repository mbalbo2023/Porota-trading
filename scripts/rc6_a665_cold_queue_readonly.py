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
import gzip
import io
import json
import os
from pathlib import Path
import re
import stat
import zlib
from zoneinfo import ZoneInfo


SCHEMA = "rc6.a667.cold-failure-cause-readonly.v3"
DATA = Path("/opt/porota-trading/data")
COLD_PARTS = ("paper_v17", "artifacts", "observer_v17.db", "dynamic-shadow", "cold-audit")
# Already-public source identities; none of their literal values leave the host.
# These are distinct historical generations, NOT interchangeable candidates.
ORIGINAL_ID = "89e7f81dbc9b4a628bdc30edf86a17b0"
ORIGINAL_CUT = "b6687dbca06ab97bc9c93d3f7940c61bba577f68d4ff4b9f887332638c0b7cb8"
ORIGINAL_MANIFEST = "4d8e93f529e4c7385710dbcff58c52bea764762eafc0190907b63b1b214f1c6d"
HISTORICAL_COLD2_ID = "7dc99ccd8f7c47849ca3705ae1cb2c49"
HISTORICAL_COLD2_CUT = "af86116a3a64a9214cfde7ccfc1e04d3165270cf6d6f6ae2b4f4ce6e13c89ad9"
HISTORICAL_COLD2_MANIFEST = "7657b124c222993f9b34471626d0c740508ee783e7fcd8ce07c85ad9495f23f3"
KNOWN_SUCCESSOR_ID = "3bfbfd6996a849f6b395f6c4a27ff15a"
KNOWN_SUCCESSOR_MANIFEST = "9893c071a5c88cada7cda6ace0f534e1424c69623eaf0eda84f0af758280f8dc"
MAX_ENTRIES = 512
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_COMPRESSED_CUT_BYTES = 8 * 1024 * 1024
MAX_DECOMPRESSED_CUT_BYTES = 8 * 1024 * 1024
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
FAILURE_CODES = frozenset(("""
SHADOW_COLD_ANONYMOUS_CAPTURE_REQUIRED SHADOW_COLD_ATOMIC_LINK_IDENTITY
SHADOW_COLD_AUDIT_REVOKED SHADOW_COLD_BACKLOG_BOUND SHADOW_COLD_BASENAME_REQUIRED
SHADOW_COLD_BATCH_PROGRESS_REQUIRED SHADOW_COLD_BATCH_TYPE
SHADOW_COLD_CANCEL_CALLBACK_INVALID SHADOW_COLD_CAPTURE_ALREADY_SEALED_OR_CLOSED
SHADOW_COLD_CAPTURE_CANCELLED SHADOW_COLD_CAPTURE_CYCLE SHADOW_COLD_CAPTURE_DEPTH
SHADOW_COLD_CAPTURE_IDENTITY_CHANGED SHADOW_COLD_COMPLETE_RECEIPT_MISSING
SHADOW_COLD_COMPUTED_ALIAS SHADOW_COLD_COMPUTED_CHANGED_AFTER_VERIFICATION
SHADOW_COLD_COMPUTED_CHANGED_DURING_VERIFICATION SHADOW_COLD_COMPUTED_HASH
SHADOW_COLD_COMPUTED_IDENTITY SHADOW_COLD_COMPUTED_RAW_BOUND
SHADOW_COLD_DEADLINE_EXPIRED_OR_CLOCK_REGRESSED SHADOW_COLD_DIRECTORY_CHANGED
SHADOW_COLD_DISK_ADMISSION_BLOCKED SHADOW_COLD_DISK_RESERVE_BREACHED
SHADOW_COLD_FAILED_BACKLOG_REQUIRES_FIX_FORWARD SHADOW_COLD_FILE_COUNT_BOUND
SHADOW_COLD_FRAME_BOUND SHADOW_COLD_FRAME_HASH SHADOW_COLD_FRAME_TYPE
SHADOW_COLD_HEADER_TRUNCATED SHADOW_COLD_HOST_MEMORY_RESERVE_BREACHED
SHADOW_COLD_LITERAL_ASCII SHADOW_COLD_MANIFEST_BINDING SHADOW_COLD_MANIFEST_BOUND
SHADOW_COLD_MANIFEST_HASH SHADOW_COLD_MONOTONIC_DEADLINE
SHADOW_COLD_MULTIPLE_FAILED_BACKLOGS SHADOW_COLD_NATIVE_CODEC_REQUIRED
SHADOW_COLD_ORIGINAL_SOURCE_BINDING SHADOW_COLD_PACK_HASH SHADOW_COLD_PACK_QUOTA
SHADOW_COLD_PENDING_BACKPRESSURE SHADOW_COLD_PRIVATE_DIRECTORY_REQUIRED
SHADOW_COLD_PRIVATE_FILE_REQUIRED SHADOW_COLD_QUEUE_CONFLICT SHADOW_COLD_QUEUE_STATE
SHADOW_COLD_REFERENCE_CONTRACT SHADOW_COLD_REFERENCE_DEPTH
SHADOW_COLD_REFERENCE_NOT_BACKWARD SHADOW_COLD_REFERENCE_SHAPE
SHADOW_COLD_ROLE_BOUND SHADOW_COLD_SEALED_INPUT_CHANGED SHADOW_COLD_SEGMENT_BOUND
SHADOW_COLD_SEGMENT_HASH SHADOW_COLD_SHORT_WRITE SHADOW_COLD_SNAPSHOT_ID
SHADOW_COLD_TRUNCATED SHADOW_COLD_WRITER_REQUIRED UNCLASSIFIED
SHADOW_AUDIT_PRUNE_INVALID SHADOW_BASENAME_REQUIRED
SHADOW_CANONICAL_STREAM_NOT_ASCII SHADOW_COMPACT_CHECKPOINT_ROLE_REQUIRED
SHADOW_COMPACT_PREOPEN_ROLE_REQUIRED SHADOW_DASHBOARD_CUT_LIMIT
SHADOW_DIRECTORY_ALIAS_FORBIDDEN SHADOW_EVIDENCE_CAPACITY_REACHED
SHADOW_EVIDENCE_DIGEST_MISMATCH SHADOW_FILE_ALIAS_FORBIDDEN
SHADOW_IMMUTABLE_FREEZE_CONFLICT SHADOW_IMMUTABLE_ROWS_LIST_REQUIRED
SHADOW_OUTPUT_MUST_BE_SEPARATE SHADOW_PAYLOAD_LIMIT
SHADOW_SNAPSHOT_CACHE_DIGEST_MISMATCH SHADOW_SNAPSHOT_CACHE_IDENTITY_MISMATCH
SHADOW_SNAPSHOT_CACHE_SIZE_MISMATCH SHADOW_STREAM_CANONICAL_ENCODER_REQUIRED
SHADOW_WRITER_LOCK_REQUIRED
""").split())
FAILURE_PHASES = frozenset(("ENTRY", "NAMESPACE", "BACKLOG_RECONCILE",
    "MANIFEST", "PRIOR_RECEIPT", "NATIVE", "INPUT_CHECK", "PACK_HASH",
    "REPORT_WRITE", "REPORT_VERIFY", "RECEIPT_COMMIT", "QUEUE_COMMIT",
    "PACK_CLEANUP"))
FAILURE_CLASSES = frozenset(("ValueError", "OSError", "RuntimeError", "KeyError",
    "TypeError", "FileNotFoundError", "TimeoutError", "MemoryError",
    "OverflowError", "AssertionError"))


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
               owner_uid: int | None = None, absent_ok: bool = False,
               maximum: int = MAX_FILE_BYTES):
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
                not 2 <= before.st_size <= maximum):
            raise Hold("PRIVATE_FILE_IDENTITY_HOLD")
        if readonly and not os.fstatvfs(handle).f_flag & os.ST_RDONLY:
            raise Hold("READONLY_MOUNT_HOLD")
        data = os.read(handle, maximum + 1)
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


def _payload(raw: bytes, *, checksum_key: str = "sha256"):
    try:
        envelope = json.loads(raw)
        if (type(envelope) is not dict or set(envelope) != {"payload", checksum_key} or
                type(envelope["payload"]) is not dict or
                not isinstance(envelope[checksum_key], str) or
                not HEX64.fullmatch(envelope[checksum_key])):
            raise ValueError("ENVELOPE_INVALID")
        canonical = json.dumps(envelope["payload"], sort_keys=True,
                               separators=(",", ":"), allow_nan=False,
                               default=str).encode()
        if not hmac.compare_digest(hashlib.sha256(canonical).hexdigest(),
                                   envelope[checksum_key]):
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


def _failure_labels(receipt):
    """Only literal labels from the deployed COLD implementation can leave host."""
    def label(field, allowed):
        value = receipt.get(field)
        return value if type(value) is str and value in allowed else "OTHER_REDACTED"
    return {"failed_error_code": label("error_code", FAILURE_CODES),
            "failed_error_phase": label("error_phase", FAILURE_PHASES),
            "failed_error_class": label("error_class", FAILURE_CLASSES)}


def _manifest_relation(fd, *, readonly, uid, ident, expected_sha):
    """Verify sealed manifest/pack inode, not a 512MiB pack hash or its contents."""
    try:
        manifest = _payload(_read_file(fd, "manifest-"+ident+".json",
                          private=True, readonly=readonly, owner_uid=uid))
        canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                               allow_nan=False, default=str).encode()
        if (not hmac.compare_digest(hashlib.sha256(canonical).hexdigest(), expected_sha)
                or manifest.get("snapshot_id") != ident
                or manifest.get("pack") != ident+".pack"
                or manifest.get("epoch") != "rc6.audit-actual-values.v1"):
            return "MANIFEST_BINDING_MISMATCH"
        pack_fd = os.open(ident+".pack", FLAGS, dir_fd=fd)
        try:
            info = os.fstat(pack_fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                    info.st_uid != uid or stat.S_IMODE(info.st_mode) & 0o077 or
                    (readonly and not os.fstatvfs(pack_fd).f_flag & os.ST_RDONLY)):
                return "PACK_METADATA_UNVERIFIED"
            identity = [info.st_dev, info.st_ino, info.st_size,
                        info.st_mtime_ns, info.st_ctime_ns]
            if (manifest.get("pack_identity") != identity or
                    manifest.get("pack_bytes") != info.st_size):
                return "PACK_METADATA_MISMATCH"
        finally:
            os.close(pack_fd)
        return "MANIFEST_AND_PACK_METADATA_BOUND_NOT_HASHED"
    except (Hold, OSError, ValueError, TypeError, KeyError):
        return "NO_VERIFICADO"


def _cut_presence(fd: int, cut: str):
    """lstat only: even a dangling symlink must not masquerade as ABSENT."""
    try:
        info = os.stat("cut-"+cut+".json.gz", dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return "ABSENT"
    except OSError:
        return "NO_VERIFICADO"
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
            stat.S_IMODE(info.st_mode) & 0o077):
        return "UNSAFE_FILE"
    return "PRESENT"


def _current_relation(data: Path, *, readonly: bool, ident: str, manifest_sha: str,
                      categories: dict):
    """Independent bounded CURRENT/cut structural binding; never republish."""
    fds = []
    try:
        fds = _open_tree(data, COLD_PARTS[:-1], readonly=readonly)
        fd = fds[-1]
        initial = os.fstat(fd)
        uid = initial.st_uid
        raw = _read_file(fd, "CURRENT.json", private=True,
                         readonly=readonly, owner_uid=uid)
        pointer = _payload(raw, checksum_key="digest")
        generation = pointer.get("generation_id")
        cut_name = pointer.get("file")
        if (type(generation) is not str or not HEX64.fullmatch(generation) or
                cut_name != "cut-"+generation+".json.gz"):
            return "CURRENT_POINTER_INVALID"
        raw_cut = _read_file(fd, cut_name, private=True,
                             readonly=readonly, owner_uid=uid,
                             maximum=MAX_COMPRESSED_CUT_BYTES)
        with gzip.GzipFile(fileobj=io.BytesIO(raw_cut), mode="rb") as stream:
            expanded = stream.read(MAX_DECOMPRESSED_CUT_BYTES+1)
        if len(expanded) > MAX_DECOMPRESSED_CUT_BYTES:
            return "CURRENT_CUT_SIZE_UNVERIFIED"
        cut = _payload(expanded, checksum_key="digest")
        cut_status = cut.get("status")
        cut_paper_zero = None
        if (type(cut_status) is dict and
                type(cut_status.get("real_orders_sent")) is int and
                type(cut_status.get("real_routes")) is str):
            cut_paper_zero = (
                cut_status["real_orders_sent"] == 0 and
                cut_status["real_routes"] == "NOT_CALLED")
        report = cut.get("report", {})
        reference = report.get("computed_report", {}) if type(report) is dict else None
        if not isinstance(reference, dict):
            return "CURRENT_REFERENCE_UNVERIFIED"
        final = os.fstat(fd)
        if (raw != _read_file(fd, "CURRENT.json", private=True,
                             readonly=readonly, owner_uid=uid) or
                raw_cut != _read_file(fd, cut_name, private=True,
                             readonly=readonly, owner_uid=uid,
                             maximum=MAX_COMPRESSED_CUT_BYTES) or
                (initial.st_ino, initial.st_mtime_ns, initial.st_ctime_ns) !=
                (final.st_ino, final.st_mtime_ns, final.st_ctime_ns)):
            return "CURRENT_POINTER_RACE_HOLD"
        categories["current_cut_paper_zero_routes_blocked"] = cut_paper_zero
        if reference.get("snapshot_id") != ident:
            return "CURRENT_OTHER_OR_NO_FAILED_REFERENCE"
        if (reference.get("state") == "PENDING" and
                reference.get("manifest_sha256") == manifest_sha and
                reference.get("schema") == "rc6.computed-audit-reference.v2"):
            categories["current_snapshot_class"] = (
                "KNOWN_SUCCESSOR" if ident == KNOWN_SUCCESSOR_ID else
                "HISTORICAL_COLD2" if ident == HISTORICAL_COLD2_ID else
                "ORIGINAL_FAILED" if ident == ORIGINAL_ID else "OTHER")
            categories["current_generation_class"] = (
                "ORIGINAL_CUT" if generation == ORIGINAL_CUT else
                "HISTORICAL_COLD2_CUT" if generation == HISTORICAL_COLD2_CUT else
                "OTHER")
            categories["original_old_cut_presence"] = _cut_presence(fd, ORIGINAL_CUT)
            categories["historical_cold2_cut_presence"] = _cut_presence(
                fd, HISTORICAL_COLD2_CUT)
            source_manifest = cut.get("manifest")
            expected_manifest = pointer.get("manifest_sha256")
            categories["current_cut_manifest_binding"] = (
                "BOUND" if type(source_manifest) is dict and
                type(expected_manifest) is str and HEX64.fullmatch(expected_manifest) and
                source_manifest.get("generation_id") == generation and
                source_manifest.get("sequence") == pointer.get("sequence") and
                hmac.compare_digest(hashlib.sha256(json.dumps(source_manifest,
                    sort_keys=True,separators=(",", ":"),allow_nan=False,
                    default=str).encode()).hexdigest(),expected_manifest)
                else "UNBOUND")
            return "CURRENT_FAILED_PENDING_REFERENCE_BOUND"
        return "CURRENT_FAILED_REFERENCE_UNBOUND"
    except (Hold, OSError, ValueError, EOFError, TypeError, KeyError, zlib.error):
        return "NO_VERIFICADO"
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
                failed.append((ident, manifest_sha, _failure_labels(receipt), raw))
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
            first = failed[0][:2]
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
        diagnostic = {"failed_error_code": "NO_VERIFICADO",
                      "failed_error_phase": "NO_VERIFICADO",
                      "failed_error_class": "NO_VERIFICADO",
                      "failed_manifest_relation": "NO_VERIFICADO",
                      "current_failed_relation": "NO_VERIFICADO",
                      "current_snapshot_class": "NO_VERIFICADO",
                      "current_generation_class": "NO_VERIFICADO",
                      "original_old_cut_presence": "NO_VERIFICADO",
                      "historical_cold2_cut_presence": "NO_VERIFICADO",
                      "current_cut_manifest_binding": "NO_VERIFICADO",
                      "current_cut_paper_zero_routes_blocked": None,
                      "known_successor_manifest_exact": None,
                      "unrecognized_failed_id_bound": None}
        if len(failed) == 1 and binding == "MATCHES_FAILED_RECEIPT":
            ident, manifest_sha, labels, receipt_raw = failed[0]
            diagnostic.update(labels)
            diagnostic["failed_manifest_relation"] = _manifest_relation(
                fd, readonly=readonly, uid=private_uid,
                ident=ident, expected_sha=manifest_sha)
            # This reader does not alter CURRENT or rescue failed evidence.
            diagnostic["current_failed_relation"] = _current_relation(
                data, readonly=readonly, ident=ident, manifest_sha=manifest_sha,
                categories=diagnostic)
            # CURRENT and the queue are separate files; catch changes across
            # their bounded reads. This remains evidence, not a host lock.
            if (os.fstat(fd).st_mtime_ns != final.st_mtime_ns or
                    _read_file(fd, "queue.json", private=True, readonly=readonly,
                               owner_uid=private_uid) != queue_raw or
                    _read_file(fd, "receipt-"+ident+".json", private=True,
                               readonly=readonly, owner_uid=private_uid) != receipt_raw):
                raise Hold("DIRECTORY_RACE_HOLD")
            if diagnostic["current_snapshot_class"] == "KNOWN_SUCCESSOR":
                diagnostic["known_successor_manifest_exact"] = (
                    hmac.compare_digest(manifest_sha, KNOWN_SUCCESSOR_MANIFEST) and
                    diagnostic["failed_manifest_relation"] ==
                    "MANIFEST_AND_PACK_METADATA_BOUND_NOT_HASHED")
            if diagnostic["current_failed_relation"] == "CURRENT_FAILED_PENDING_REFERENCE_BOUND":
                diagnostic["unrecognized_failed_id_bound"] = (
                    diagnostic["current_snapshot_class"] == "OTHER" and
                    diagnostic["current_generation_class"] == "OTHER" and
                    diagnostic["current_cut_manifest_binding"] == "BOUND" and
                    diagnostic["failed_manifest_relation"] ==
                    "MANIFEST_AND_PACK_METADATA_BOUND_NOT_HASHED")
        return {"receipt_count": len(receipts),
                "failed_receipt_count": len(failed),
                "queue_state": queue_state, "queue_failed_binding": binding,
                "folder_entries_checked": len(names),
                "envelopes_hash_verified": True,
                "metadata_snapshot_stable": True, **diagnostic}
    finally:
        for fd in reversed(fds):
            os.close(fd)


def _live_diagnostic(data: Path, *, readonly: bool):
    """Read only the bounded status envelope; never export its raw contents."""
    result = {"live_status_class": "NO_VERIFICADO",
              "live_cancel_site_class": "NO_VERIFICADO",
               "live_error_origin_class": "NO_VERIFICADO",
              "live_paper_zero_routes_blocked": None}
    fds = []
    try:
        fds = _open_tree(data, COLD_PARTS[:-1], readonly=readonly)
        fd = fds[-1]
        uid = os.fstat(fd).st_uid
        raw = _read_file(fd, "status.json", private=True, readonly=readonly,
                         owner_uid=uid)
        status = _payload(raw, checksum_key="digest")
        if raw != _read_file(fd, "status.json", private=True,
                             readonly=readonly, owner_uid=uid):
            return result
        state = status.get("status")
        result["live_status_class"] = (
            "SHADOW_OBSERVING" if state == "SHADOW_OBSERVING" else
            "FAIL_CLOSED" if state == "FAIL_CLOSED" else "OTHER")
        orders = status.get("real_orders_sent")
        routes = status.get("real_routes")
        if type(orders) is int and type(routes) is str:
            result["live_paper_zero_routes_blocked"] = (
                orders == 0 and routes == "NOT_CALLED")
        if (state == "FAIL_CLOSED" and status.get("error_class") == "ValueError"
                and status.get("error_module") == "cold_audit"):
            line = status.get("error_line")
            result["live_cancel_site_class"] = (
                "FRAME_265" if type(line) is int and line == 265 else
                "STAGE_360" if type(line) is int and line == 360 else
                "SEAL_386_NOT_WHITELISTED" if type(line) is int and line == 386
                else "OTHER")
            # Line sites checked against the exact 7e1e... product cold_audit.py.
            # These are provenance labels, NEVER proof of clean stop/quiescence.
            result["live_error_origin_class"] = (
                "MEMORY_RESERVE_GUARD_55" if type(line) is int and line == 55 else
                "FAILED_BACKLOG_GUARD" if type(line) is int and line in (595, 628, 665) else
                "CANCEL_SITE_265_360_386" if type(line) is int and line in (265, 360, 386) else
                "OTHER_COLD_AUDIT_LINE")
        else:
            result["live_cancel_site_class"] = "NOT_COLD_AUDIT_VALUE_ERROR"
            result["live_error_origin_class"] = "NOT_COLD_AUDIT_VALUE_ERROR"
    except (Hold, OSError, ValueError, TypeError, KeyError):
        pass
    finally:
        for fd in reversed(fds):
            os.close(fd)
    return result


def _one_archive_metadata(fd: int, *, readonly: bool, ident: str,
                          expected_manifest: str, historical: bool,
                          detail: dict | None = None):
    """Never open or hash the large .pack; verify only metadata and small receipts."""
    try:
        archive_fd = os.open("rc6-failed-cold-"+ident, DIRECTORY_FLAGS, dir_fd=fd)
    except FileNotFoundError:
        return "ABSENT"
    except OSError:
        return "NO_VERIFICADO"
    try:
        info = _check_directory(archive_fd, readonly=readonly, private=True)
        initial = os.fstat(archive_fd)
        names = os.listdir(archive_fd)
        expected = {ident+".pack", "manifest-"+ident+".json",
                    "receipt-"+ident+".json", "queue.json"}
        if historical:
            expected.add("quarantine.json")
        if len(names) > 8 or set(names) != expected:
            return "INCOMPLETE_OR_EXTRA"
        uid = info.st_uid
        manifest = _payload(_read_file(archive_fd, "manifest-"+ident+".json",
                private=True, readonly=readonly, owner_uid=uid))
        receipt = _payload(_read_file(archive_fd, "receipt-"+ident+".json",
                private=True, readonly=readonly, owner_uid=uid))
        queue = _payload(_read_file(archive_fd, "queue.json", private=True,
                readonly=readonly, owner_uid=uid))
        digest = hashlib.sha256(json.dumps(manifest,sort_keys=True,
                separators=(",", ":"),allow_nan=False,default=str).encode()).hexdigest()
        if (not hmac.compare_digest(digest, expected_manifest) or
                manifest.get("snapshot_id") != ident or
                manifest.get("pack") != ident+".pack" or
                receipt.get("snapshot_id") != ident or receipt.get("state") != "FAILED" or
                receipt.get("manifest_sha256") != expected_manifest or
                queue.get("snapshot_id") != ident or queue.get("state") != "FAILED" or
                queue.get("manifest_sha256") != expected_manifest):
            return "SMALL_RECEIPT_MISMATCH"
        if historical:
            quarantine = _payload(_read_file(archive_fd, "quarantine.json",
                        private=True, readonly=readonly, owner_uid=uid),
                        checksum_key="digest")
            if (quarantine.get("state") != "QUARANTINED_FAILED" or
                    quarantine.get("snapshot_id") != ident or
                    quarantine.get("manifest_sha256") != expected_manifest or
                    type(manifest.get("pack_sha256")) is not str or
                    HEX64.fullmatch(manifest["pack_sha256"]) is None or
                    quarantine.get("pack_sha256") != manifest.get("pack_sha256") or
                    type(quarantine.get("real_orders_sent")) is not int or
                    quarantine.get("real_orders_sent") != 0 or
                    quarantine.get("old_cut_revoked") is not True or
                    quarantine.get("revoked_cut") != HISTORICAL_COLD2_CUT):
                return "SMALL_RECEIPT_MISMATCH"
        # lstat: a symlink/hardlink cannot inherit a valid historical label.
        pack = os.stat(ident+".pack", dir_fd=archive_fd, follow_symlinks=False)
        original_identity = manifest.get("pack_identity")
        cause = (
            "PACK_TYPE_LINK_MODE" if not stat.S_ISREG(pack.st_mode) or
                pack.st_nlink != 1 or stat.S_IMODE(pack.st_mode)&0o077 else
            "PACK_OWNER" if pack.st_uid != uid else
            "PACK_BYTES" if type(manifest.get("pack_bytes")) is not int or
                manifest["pack_bytes"] != pack.st_size else
            "IDENTITY_SHAPE" if type(original_identity) is not list or
                len(original_identity) != 5 or
                any(type(value) is not int for value in original_identity) else
            "PACK_DEV_INO" if original_identity[:2] != [pack.st_dev, pack.st_ino] else
            "PACK_IDENTITY_SIZE" if original_identity[2] != pack.st_size else
            "PACK_MTIME" if original_identity[3] != pack.st_mtime_ns else None)
        if cause is not None:
            if detail is not None:
                detail["cause"] = cause
            return "PACK_METADATA_UNVERIFIED"
        final = os.fstat(archive_fd)
        if (set(os.listdir(archive_fd)) != set(names) or
                (initial.st_dev, initial.st_ino, initial.st_mtime_ns,
                 initial.st_ctime_ns) !=
                (final.st_dev, final.st_ino, final.st_mtime_ns,
                 final.st_ctime_ns)):
            return "NO_VERIFICADO"
        if detail is not None:
            detail["cause"] = "NONE"
        return "METADATA_BOUND_PACK_NOT_HASHED"
    except (Hold, OSError, ValueError, TypeError, KeyError):
        return "NO_VERIFICADO"
    finally:
        os.close(archive_fd)


def _archive_diagnostic(data: Path, *, readonly: bool):
    result = {"original_archive_class": "NO_VERIFICADO",
              "historical_cold2_archive_class": "NO_VERIFICADO",
              "historical_cold2_pack_metadata_cause": "NO_VERIFICADO"}
    fds = []
    try:
        fds = _open_tree(data, ("deploy",), readonly=readonly)
        fd = fds[-1]
        result["original_archive_class"] = _one_archive_metadata(
            fd, readonly=readonly, ident=ORIGINAL_ID,
            expected_manifest=ORIGINAL_MANIFEST, historical=False)
        cause = {"cause": "NOT_APPLICABLE"}
        result["historical_cold2_archive_class"] = _one_archive_metadata(
            fd, readonly=readonly, ident=HISTORICAL_COLD2_ID,
            expected_manifest=HISTORICAL_COLD2_MANIFEST, historical=True,
            detail=cause)
        result["historical_cold2_pack_metadata_cause"] = cause["cause"]
    except (Hold, OSError, ValueError, TypeError):
        pass
    finally:
        for fd in reversed(fds):
            os.close(fd)
    return result


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
        "failed_error_code": "NO_VERIFICADO", "failed_error_phase": "NO_VERIFICADO",
        "failed_error_class": "NO_VERIFICADO",
        "failed_manifest_relation": "NO_VERIFICADO",
        "current_failed_relation": "NO_VERIFICADO",
        "current_snapshot_class": "NO_VERIFICADO",
        "current_generation_class": "NO_VERIFICADO",
        "original_old_cut_presence": "NO_VERIFICADO",
        "historical_cold2_cut_presence": "NO_VERIFICADO",
        "current_cut_manifest_binding": "NO_VERIFICADO",
        "current_cut_paper_zero_routes_blocked": None,
        "known_successor_manifest_exact": None,
        "unrecognized_failed_id_bound": None,
        "live_status_class": "NO_VERIFICADO",
        "live_cancel_site_class": "NO_VERIFICADO",
        "live_error_origin_class": "NO_VERIFICADO",
        "live_paper_zero_routes_blocked": None,
        "original_archive_class": "NO_VERIFICADO",
        "historical_cold2_archive_class": "NO_VERIFICADO",
        "historical_cold2_pack_metadata_cause": "NO_VERIFICADO",
        # Explicit scope exclusions: no stop receipt/report or pack content read.
        "quiescence_verified": False, "computed_report_hash_verified": False,
        "pack_content_hash_verified": False, "runtime_running_verified": False,
        "envelopes_hash_verified": False, "metadata_snapshot_stable": False,
        "paper_admission_verified": False, "causal_fix_verified": False,
    }
    try:
        observed = _provenance(data, readonly=readonly)
        counts = _counts(data, readonly=readonly)
        result.update(counts)
        result.update(_live_diagnostic(data, readonly=readonly))
        result.update(_archive_diagnostic(data, readonly=readonly))
        result.update(state="READ_ONLY_OBSERVED", reason="COUNTS_AND_CAUSE_ONLY_NO_REPAIR",
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
