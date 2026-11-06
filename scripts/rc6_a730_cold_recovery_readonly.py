#!/usr/bin/env python3
"""A730: physical COLD recovery predicates only; no recovery or host writes.

Runs in GitHub Actions under a private read-only bind of Porota data. Checks
small COLD envelopes, legacy archives, and a computed report *only* when a
COMPLETE receipt is present. A FAILED queue is never treated as recoverable.
Do not import/call production COLD code: its ensure_idle() writes receipts.
"""

from __future__ import annotations

from contextlib import closing
import gzip
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import zlib


SCHEMA = "rc6.a730.cold-recovery-readonly.v1"
DATA = Path("/opt/porota-trading/data")
DEPLOY = DATA / "deploy"
SHADOW = DATA / "paper_v17/artifacts/observer_v17.db/dynamic-shadow"
MARKER = DEPLOY / "CURRENT_STATE_V2.json"
OLD_ID = "89e7f81dbc9b4a628bdc30edf86a17b0"
OLD_MANIFEST = "4d8e93f529e4c7385710dbcff58c52bea764762eafc0190907b63b1b214f1c6d"
HIST_ID = "7dc99ccd8f7c47849ca3705ae1cb2c49"
HIST_MANIFEST = "7657b124c222993f9b34471626d0c740508ee783e7fcd8ce07c85ad9495f23f3"
HIST_CUT = "af86116a3a64a9214cfde7ccfc1e04d3165270cf6d6f6ae2b4f4ce6e13c89ad9"
OLD_CUT = "b6687dbca06ab97bc9c93d3f7940c61bba577f68d4ff4b9f887332638c0b7cb8"
HEX40 = re.compile(r"[a-f0-9]{40}\Z")
HEX32 = re.compile(r"[a-f0-9]{32}\Z")
HEX64 = re.compile(r"[a-f0-9]{64}\Z")
IMAGE = re.compile(r"sha256:[a-f0-9]{64}\Z")
MAX_SMALL = 256 * 1024
MAX_CUT = 8 * 1024 * 1024
MAX_COMPRESSED_REPORT = 512 * 1024 * 1024
MAX_RAW_REPORT = 512 * 1024 * 1024
MAX_REPORT_SECONDS = 75
ARCHIVE_STATES = {"ABSENT", "UNSAFE_ARCHIVE", "NAMESPACE_UNEXPECTED",
    "SMALL_RECEIPTS_UNBOUND", "PACK_OWNER", "PACK_METADATA_MISMATCH",
    "PACK_METADATA_BOUND_NOT_HASHED", "NO_VERIFICADO"}
SOURCE_STATES = {"NO_VERIFICADO", "CURRENT_UNAVAILABLE", "CURRENT_REFERENCE_UNBOUND",
    "HISTORICAL_OR_REVOKED_CUT", "QUEUE_UNAVAILABLE", "QUEUE_FAILED",
    "QUEUE_PENDING", "QUEUE_COMPLETE", "QUEUE_OTHER", "MANIFEST_UNBOUND",
    "RECEIPT_FAILED", "RECEIPT_COMPLETE", "RECEIPT_OTHER"}
REPORT_STATES = {"NOT_APPLICABLE", "BLOCKED_FAILED_QUEUE", "RECEIPT_NOT_COMPLETE",
    "COMPLETE_RECORD_INVALID", "FILE_UNAVAILABLE", "FILE_METADATA_MISMATCH",
    "REPORT_OVERSIZE", "REPORT_TIMEOUT", "REPORT_HASH_MATCH",
    "REPORT_HASH_MISMATCH", "REPORT_FILE_RACE"}


class Hold(ValueError):
    """Classified privately; exception text is never exported."""


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _parent(path: Path) -> None:
    try:
        relative = path.parent.relative_to(DATA)
    except ValueError as exc:
        raise Hold("OUT_OF_SCOPE") from exc
    current = DATA
    for part in (".", *relative.parts):
        if part != ".":
            current = current / part
        info = current.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise Hold("DIR_ALIAS")


def _regular(path: Path, limit: int = MAX_SMALL) -> bytes:
    _parent(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                not 1 <= before.st_size <= limit or
                not os.fstatvfs(fd).f_flag & os.ST_RDONLY):
            raise Hold("FILE_NOT_BOUNDED_RO")
        raw = os.read(fd, limit + 1)
        if len(raw) != before.st_size or _identity(os.fstat(fd)) != _identity(before):
            raise Hold("SMALL_FILE_RACE")
        return raw
    finally:
        os.close(fd)


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False, ensure_ascii=True).encode("ascii")


def _envelope(path: Path, *, cold: bool = False, zipped: bool = False) -> dict:
    raw = _regular(path, MAX_CUT if zipped else MAX_SMALL)
    if zipped:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            raw = stream.read(MAX_CUT + 1)
        if len(raw) > MAX_CUT:
            raise Hold("CUT_UNCOMPRESSED_BOUND")
    row = json.loads(raw)
    key = "sha256" if cold else "digest"
    if (type(row) is not dict or set(row) != {"payload", key} or
            type(row["payload"]) is not dict or
            type(row[key]) is not str or not HEX64.fullmatch(row[key]) or
            not hmac.compare_digest(hashlib.sha256(_canonical(row["payload"])).hexdigest(),
                                    row[key])):
        raise Hold("ENVELOPE_INVALID")
    return row["payload"]


def _marker(r1: str, r2: str, candidate: str) -> dict:
    result = {"state": "NO_VERIFICADO", "relation": "UNKNOWN",
              "paper_zero": None, "routes_blocked": None}
    try:
        data = json.loads(_regular(MARKER))
        if (type(data) is not dict or data.get("schema_version") != 2 or
                type(data.get("deploy_sha")) is not str or
                not HEX40.fullmatch(data["deploy_sha"]) or
                type(data.get("candidate_sha")) is not str or
                not HEX40.fullmatch(data["candidate_sha"]) or
                type(data.get("image_id")) is not str or
                not IMAGE.fullmatch(data["image_id"]) or
                data.get("validation_status") not in
                {"VALIDATED_RUNTIME", "DEPLOYED_VALIDATION_PENDING"}):
            return result
        relation = ("VALIDATED_R1" if data["deploy_sha"] == r1 and
                    data["validation_status"] == "VALIDATED_RUNTIME" else
                    "PENDING_R2" if data["deploy_sha"] == r2 and
                    data["candidate_sha"] == candidate and
                    data["validation_status"] == "DEPLOYED_VALIDATION_PENDING" else
                    "OTHER_EXACT_SHA")
        return {"state": "OBSERVED", "relation": relation,
                "paper_zero": data.get("mode") == "PRODUCTION_PAPER" and
                    type(data.get("real_orders_sent")) is int and
                    data["real_orders_sent"] == 0,
                "routes_blocked": data.get("real_order_routes") == "NOT_CALLED"}
    except (OSError, Hold, ValueError, TypeError, KeyError):
        return result


def _archive(ident: str, expected_manifest: str, historical: bool) -> str:
    """Legacy archive: only names/owners/digests of SMALL files; no pack open."""
    path = DEPLOY / ("rc6-failed-cold-" + ident)
    try:
        _parent(path)
        if not path.exists():
            return "ABSENT"
        _parent(path / "queue.json")
        info = path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or
                stat.S_IMODE(info.st_mode) & 0o077):
            return "UNSAFE_ARCHIVE"
        expected = {ident + ".pack", "manifest-" + ident + ".json",
                    "receipt-" + ident + ".json", "queue.json"}
        if historical:
            expected.add("quarantine.json")
        if {name.name for name in path.iterdir()} != expected:
            return "NAMESPACE_UNEXPECTED"
        manifest = _envelope(path / ("manifest-" + ident + ".json"), cold=True)
        receipt = _envelope(path / ("receipt-" + ident + ".json"), cold=True)
        queue = _envelope(path / "queue.json", cold=True)
        digest = hashlib.sha256(_canonical(manifest)).hexdigest()
        if (digest != expected_manifest or manifest.get("snapshot_id") != ident or
                manifest.get("pack") != ident + ".pack" or
                receipt.get("snapshot_id") != ident or
                receipt.get("state") != "FAILED" or
                receipt.get("manifest_sha256") != digest or
                queue.get("state") != "FAILED" or
                queue.get("snapshot_id") != ident or
                queue.get("manifest_sha256") != digest):
            return "SMALL_RECEIPTS_UNBOUND"
        if historical:
            quarantine = _envelope(path / "quarantine.json")
            if (quarantine.get("state") != "QUARANTINED_FAILED" or
                    quarantine.get("snapshot_id") != ident or
                    quarantine.get("manifest_sha256") != digest or
                    quarantine.get("pack_sha256") != manifest.get("pack_sha256") or
                    type(quarantine.get("real_orders_sent")) is not int or
                    quarantine["real_orders_sent"] != 0 or
                    quarantine.get("old_cut_revoked") is not True or
                    quarantine.get("revoked_cut") != HIST_CUT):
                return "SMALL_RECEIPTS_UNBOUND"
        pack_info = (path / (ident + ".pack")).lstat()
        if (not stat.S_ISREG(pack_info.st_mode) or pack_info.st_nlink != 1 or
                stat.S_IMODE(pack_info.st_mode) & 0o077):
            return "PACK_METADATA_MISMATCH"
        if pack_info.st_uid != info.st_uid:
            return "PACK_OWNER"
        if (manifest.get("pack_bytes") != pack_info.st_size or
                manifest.get("pack_identity") != list(_identity(pack_info)) or
                type(manifest.get("pack_sha256")) is not str or
                not HEX64.fullmatch(manifest["pack_sha256"])):
            return "PACK_METADATA_MISMATCH"
        return "PACK_METADATA_BOUND_NOT_HASHED"
    except (OSError, Hold, ValueError, TypeError, KeyError, AttributeError):
        return "NO_VERIFICADO"


def _verify_computed(path: Path, record: dict) -> str:
    """Never reads a PENDING/FAILED report; streaming gzip when COMPLETE only."""
    if type(record) is not dict:
        return "COMPLETE_RECORD_INVALID"
    sha = record.get("sha256")
    if (type(sha) is not str or not HEX64.fullmatch(sha) or
            record.get("file") != "computed-" + sha + ".json.gz" or
            type(record.get("raw_bytes")) is not int or
            type(record.get("compressed_bytes")) is not int or
            not 0 < record["raw_bytes"] <= MAX_RAW_REPORT or
            not 0 < record["compressed_bytes"] <= MAX_COMPRESSED_REPORT):
        return "COMPLETE_RECORD_INVALID"
    fd = -1
    try:
        _parent(path)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_size != record["compressed_bytes"] or
                not os.fstatvfs(fd).f_flag & os.ST_RDONLY):
            return "FILE_METADATA_MISMATCH"
        deadline = time.monotonic() + MAX_REPORT_SECONDS
        length = 0
        digest = hashlib.sha256()
        # Attach the gzip reader to a duplicate; the original fd remains open
        # for after-read inode+size+mtime+ctime proof.
        with os.fdopen(os.dup(fd), "rb", closefd=True) as stream:
            with gzip.GzipFile(fileobj=stream) as zipped:
                while True:
                    if time.monotonic() > deadline:
                        return "REPORT_TIMEOUT"
                    chunk = zipped.read(1_048_576)
                    if not chunk:
                        break
                    length += len(chunk)
                    if length > MAX_RAW_REPORT:
                        return "REPORT_OVERSIZE"
                    digest.update(chunk)
        if _identity(before) != _identity(os.fstat(fd)):
            return "REPORT_FILE_RACE"
        if length != record["raw_bytes"]:
            return "REPORT_HASH_MISMATCH"
        return ("REPORT_HASH_MATCH" if hmac.compare_digest(digest.hexdigest(), sha)
                else "REPORT_HASH_MISMATCH")
    except (OSError, Hold, ValueError, TypeError, gzip.BadGzipFile,
            EOFError, zlib.error):
        return "FILE_UNAVAILABLE"
    finally:
        if fd >= 0:
            os.close(fd)


def _current(shadow: Path) -> dict:
    result = {"source_state": "CURRENT_UNAVAILABLE", "cut_hash_bound": None,
              "reference_bound": None, "failed_queue": None,
              "computed_report_state": "NOT_APPLICABLE",
              "computed_report_hash_verified": False,
              "successor_recovery_proven": False}
    try:
        pointer = _envelope(shadow / "CURRENT.json")
        gen = pointer.get("generation_id")
        if (type(gen) is not str or not HEX64.fullmatch(gen) or
                pointer.get("file") != "cut-" + gen + ".json.gz"):
            return result
        cut = _envelope(shadow / pointer["file"], zipped=True)
        cut_manifest = cut.get("manifest")
        reference = cut.get("report", {}).get("computed_report")
        if (type(cut_manifest) is not dict or type(reference) is not dict or
                cut_manifest.get("generation_id") != gen or
                cut_manifest.get("sequence") != pointer.get("sequence") or
                type(pointer.get("manifest_sha256")) is not str or
                not HEX64.fullmatch(pointer["manifest_sha256"])):
            return result
        bound = hmac.compare_digest(hashlib.sha256(_canonical(cut_manifest)).hexdigest(),
                                    pointer["manifest_sha256"])
        result["cut_hash_bound"] = bound
        snapshot = reference.get("snapshot_id")
        if (not bound or type(snapshot) is not str or not HEX32.fullmatch(snapshot) or
                snapshot in {OLD_ID, HIST_ID} or gen in {OLD_CUT, HIST_CUT}):
            result["source_state"] = "HISTORICAL_OR_REVOKED_CUT"
            return result
        source = shadow / "cold-audit"
        result["source_state"] = "QUEUE_UNAVAILABLE"
        queue = _envelope(source / "queue.json", cold=True)
        queue_state = queue.get("state")
        result["failed_queue"] = queue_state == "FAILED"
        if queue.get("snapshot_id") != snapshot or queue_state not in {
                "FAILED", "PENDING", "COMPLETE"}:
            result["source_state"] = "QUEUE_OTHER"
            return result
        result["source_state"] = "QUEUE_" + queue_state
        manifest = _envelope(source / ("manifest-" + snapshot + ".json"), cold=True)
        sha = hashlib.sha256(_canonical(manifest)).hexdigest()
        ref_bound = (reference.get("schema") == "rc6.computed-audit-reference.v2" and
                     reference.get("state") == "PENDING" and
                     reference.get("snapshot_id") == snapshot and
                     reference.get("manifest_sha256") == sha and
                     manifest.get("snapshot_id") == snapshot and
                     manifest.get("pack") == snapshot + ".pack" and
                     queue.get("manifest_sha256") == sha)
        result["reference_bound"] = ref_bound
        if not ref_bound:
            result["source_state"] = "MANIFEST_UNBOUND"
            return result
        receipt = _envelope(source / ("receipt-" + snapshot + ".json"), cold=True)
        if (receipt.get("snapshot_id") != snapshot or
                receipt.get("manifest_sha256") != sha):
            result["source_state"] = "RECEIPT_OTHER"
            return result
        if receipt.get("state") == "FAILED":
            result["source_state"] = "RECEIPT_FAILED"
            result["computed_report_state"] = "BLOCKED_FAILED_QUEUE" if queue_state == "FAILED" else "RECEIPT_NOT_COMPLETE"
            return result
        if receipt.get("state") != "COMPLETE":
            result["source_state"] = "RECEIPT_OTHER"
            return result
        result["source_state"] = "RECEIPT_COMPLETE"
        if queue_state != "COMPLETE":
            result["computed_report_state"] = "RECEIPT_NOT_COMPLETE"
            return result
        computed = receipt.get("computed_report")
        ident = receipt.get("computed_identity")
        if (type(ident) is not list or len(ident) != 5 or
                any(type(value) is not int for value in ident) or
                type(computed) is not dict):
            result["computed_report_state"] = "COMPLETE_RECORD_INVALID"
            return result
        sha = computed.get("sha256")
        if type(sha) is not str or not HEX64.fullmatch(sha):
            result["computed_report_state"] = "COMPLETE_RECORD_INVALID"
            return result
        path = shadow / ("computed-" + sha + ".json.gz")
        # Identity equality is checked inside the open streaming read as well;
        # we never trust a receipt's pathname or accept aliases.
        try:
            _parent(path)
            info = path.lstat()
            if list(_identity(info)) != ident:
                result["computed_report_state"] = "FILE_METADATA_MISMATCH"
                return result
        except OSError:
            result["computed_report_state"] = "FILE_UNAVAILABLE"
            return result
        status = _verify_computed(path, computed)
        result["computed_report_state"] = status
        result["computed_report_hash_verified"] = status == "REPORT_HASH_MATCH"
        # Report complete is NOT by itself R3 recovery proof: the successor
        # needs own eligibility/marker/quiescence gates before deploying.
    except (OSError, Hold, ValueError, TypeError, KeyError, AttributeError,
            gzip.BadGzipFile, EOFError, zlib.error):
        pass
    return result


def observe(r1: str, r2: str, candidate: str) -> dict:
    try:
        if not os.statvfs(DATA).f_flag & os.ST_RDONLY:
            raise Hold("HOST_DATA_NOT_READONLY")
    except (OSError, Hold):
        return {"schema": SCHEMA, "state": "NO_VERIFICADO", "read_only": True}
    return {"schema": SCHEMA, "state": "DIAGNOSTIC_ONLY", "read_only": True,
            "ro_mount_verified": True, "mutations": 0, "provider_gets": 0,
            "routes_called": False, "runtime_validated": False,
            "marker": _marker(r1, r2, candidate),
            "current": _current(SHADOW),
            "legacy_original": _archive(OLD_ID, OLD_MANIFEST, False),
            "legacy_cold2": _archive(HIST_ID, HIST_MANIFEST, True)}


def sanitize(raw: str, r1: str, r2: str, candidate: str) -> dict:
    fallback = {"schema": SCHEMA, "state": "NO_VERIFICADO", "read_only": True,
                "mutations": 0, "provider_gets": 0, "runtime_validated": False}
    try:
        if len(raw) > 4096 or any(not HEX40.fullmatch(x) for x in (r1, r2, candidate)):
            raise Hold("INPUT_UNSAFE")
        obj = json.loads(raw)
        if (type(obj) is not dict or set(obj) != {"schema", "state", "read_only",
                "ro_mount_verified", "mutations", "provider_gets", "routes_called",
                "runtime_validated", "marker", "current", "legacy_original",
                "legacy_cold2"} or obj["schema"] != SCHEMA or
                obj["state"] != "DIAGNOSTIC_ONLY" or obj["read_only"] is not True or
                obj["ro_mount_verified"] is not True or
                type(obj["mutations"]) is not int or obj["mutations"] != 0 or
                type(obj["provider_gets"]) is not int or obj["provider_gets"] != 0 or
                obj["routes_called"] is not False or obj["runtime_validated"] is not False):
            raise Hold("HOST_INVARIANT_UNSAFE")
        marker = obj["marker"]
        if (type(marker) is not dict or set(marker) !=
                {"state", "relation", "paper_zero", "routes_blocked"} or
                marker["state"] not in {"OBSERVED", "NO_VERIFICADO"} or
                marker["relation"] not in {"UNKNOWN", "VALIDATED_R1", "PENDING_R2",
                                          "OTHER_EXACT_SHA"} or
                any(marker[k] is not None and type(marker[k]) is not bool for k in
                    ("paper_zero", "routes_blocked"))):
            raise Hold("MARKER_UNSAFE")
        current = obj["current"]
        if (type(current) is not dict or set(current) !=
                {"source_state", "cut_hash_bound", "reference_bound",
                 "failed_queue", "computed_report_state", "computed_report_hash_verified",
                 "successor_recovery_proven"} or
                current["source_state"] not in SOURCE_STATES or
                current["computed_report_state"] not in REPORT_STATES or
                type(current["computed_report_hash_verified"]) is not bool or
                current["computed_report_hash_verified"] !=
                (current["computed_report_state"] == "REPORT_HASH_MATCH") or
                current["successor_recovery_proven"] is not False or
                any(current[k] is not None and type(current[k]) is not bool for k in
                    ("cut_hash_bound", "reference_bound", "failed_queue"))):
            raise Hold("CURRENT_UNSAFE")
        for key in ("legacy_original", "legacy_cold2"):
            if type(obj[key]) is not str or obj[key] not in ARCHIVE_STATES:
                raise Hold("LEGACY_UNSAFE")
        # Reconstruct exact public receipt: no nested key or text passthrough.
        return {"schema": SCHEMA, "state": "DIAGNOSTIC_ONLY", "read_only": True,
                "mutations": 0, "provider_gets": 0, "runtime_validated": False,
                "expected_previous_sha": r1, "failed_deploy_sha": r2,
                "failed_candidate_sha": candidate,
                "marker": {k: marker[k] for k in
                           ("state", "relation", "paper_zero", "routes_blocked")},
                "current": {k: current[k] for k in
                            ("source_state", "cut_hash_bound", "reference_bound",
                             "failed_queue", "computed_report_state",
                             "computed_report_hash_verified", "successor_recovery_proven")},
                "legacy_original": obj["legacy_original"],
                "legacy_cold2": obj["legacy_cold2"]}
    except (OSError, Hold, ValueError, TypeError, KeyError, OverflowError):
        return fallback


def main() -> int:
    expected = [os.environ.get(name, "") for name in
                ("RC6_RO_PREVIOUS_SHA", "RC6_RO_FAILED_DEPLOY_SHA",
                 "RC6_RO_FAILED_CANDIDATE_SHA")]
    if "--sanitize" in sys.argv:
        obj = sanitize(sys.stdin.read(4097), *expected)
        print(json.dumps(obj, sort_keys=True, separators=(",", ":")))
        return 0 if obj["state"] == "DIAGNOSTIC_ONLY" else 2
    if "--host" not in sys.argv or any(not HEX40.fullmatch(s) for s in expected) or \
            len(set(expected)) != 3:
        return 2
    obj = observe(*expected)
    print(json.dumps(obj, sort_keys=True, separators=(",", ":")))
    return 0 if obj["state"] == "DIAGNOSTIC_ONLY" else 2


if __name__ == "__main__":
    sys.exit(main())
