"""Bounded RAW custody for the effective V3 writer's original five members.

The recorder writes only data evidence. A G5P decision additionally requires
authenticated Actions origin, the original Horizon FIN and an admissible
physical model. A partial capture can never be certified.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat

from rc6_shadow_runtime.archive_components import MEMBERS
from rc6_shadow_runtime.archive_physical_model import (
    HORIZON_CUTS, original_prefix_obligations,
    require_original_bytes_before_all1202_model,
)

SCHEMA = "rc6.g5p-paper-v3-original-frames.v1"
FRAME_SCHEMA = "rc6.g5p-paper-v3-original-five.v1"
MAX_FRAME_BYTES = 16 * 1024**2
# RAW transfer/staging bound. This is outside the native V3 512 MiB archive
# namespace and never substitutes for its measured quota or FIN.
MAX_CAPTURE_BYTES = 1024 * 1024**2
RESERVE_BYTES = 2 * 1024**3
MIN_FREE_INODE_PERCENT = 10
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


def require(value, reason):
    if not value:
        raise ValueError(reason)


def wire(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def parse(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, "G5P_DUPLICATE_JSON_KEY")
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: require(False, "G5P_NONFINITE_JSON"))


def capacity(root, additional_bytes, additional_inodes=1):
    """Measure the same filesystem before each new RAW object, with no GC credit."""
    fs = os.statvfs(root)
    require(fs.f_frsize > 0 and fs.f_files > 0 and fs.f_favail >= 0,
            "G5P_FILESYSTEM_CAPACITY_UNKNOWN")
    require(fs.f_bavail * fs.f_frsize >= additional_bytes + RESERVE_BYTES
            and (fs.f_favail - additional_inodes) * 100 >= fs.f_files * MIN_FREE_INODE_PERCENT,
            "G5P_V3_NATIVE_RESERVE_OR_INODES_INSUFFICIENT")
    return {"free_bytes_before": fs.f_bavail * fs.f_frsize,
            "free_inodes_before": fs.f_favail, "total_inodes": fs.f_files,
            "reserve_bytes": RESERVE_BYTES,
            "minimum_free_inode_percent": MIN_FREE_INODE_PERCENT,
            "new_bytes_upper": additional_bytes, "new_inodes": additional_inodes}


class FrameRecorder:
    def __init__(self, root, *, source_sha, source_tree, owner_session, run_id,
                 run_attempt, writer_binding_sha256):
        self.root = Path(root).absolute()
        require(not os.path.lexists(self.root) and not any(p.is_symlink() for p in self.root.parents)
                and self.root.parent.lstat().st_uid == os.getuid(),
                "G5P_FRESH_OWN_FRAME_ROOT_REQUIRED")
        require(HEX40.fullmatch(source_sha or "") and HEX40.fullmatch(source_tree or "")
                and HEX64.fullmatch(writer_binding_sha256 or "")
                and type(owner_session) is str and bool(owner_session)
                and type(run_id) is int and run_id > 0 and run_attempt == 1
                and type(run_attempt) is int,
                "G5P_EXACT_SOURCE_OWNER_FIRST_RUN_REQUIRED")
        self.root.mkdir(mode=0o700)
        self.binding = dict(source_sha=source_sha, source_tree=source_tree,
                            owner_session=owner_session, run_id=run_id,
                            run_attempt=run_attempt,
                            writer_binding_sha256=writer_binding_sha256)
        self.frames = []
        self.bytes_written = 0

    def record(self, index, *, as_of, originals, receipt, restored):
        require(type(index) is int and index == len(self.frames) and index < HORIZON_CUTS,
                "G5P_ORIGINAL_CUT_SEQUENCE_REQUIRED")
        require(type(originals) is dict and set(originals) == set(MEMBERS)
                and all(type(raw) is bytes for raw in originals.values())
                and restored == originals,
                "G5P_FIVE_ORIGINAL_MEMBERS_NOT_RESTORED")
        require(type(receipt) is dict and type(as_of) is str
                and receipt.get("source_as_of") == as_of,
                "G5P_ORIGINAL_RECEIPT_CLOCK_MISMATCH")
        frame = {"schema": FRAME_SCHEMA, **self.binding, "cut_index": index,
                 "as_of": as_of, "receipt": receipt,
                 "originals": {name: {"bytes": len(value),
                                      "sha256": hashlib.sha256(value).hexdigest(),
                                      "raw_base64": base64.b64encode(value).decode("ascii")}
                               for name, value in sorted(originals.items())}}
        compressed = gzip.compress(wire(frame), mtime=0)
        require(0 < len(compressed) <= MAX_FRAME_BYTES
                and self.bytes_written + len(compressed) <= MAX_CAPTURE_BYTES,
                "G5P_ORIGINAL_FRAME_CAPTURE_BOUND_EXCEEDED")
        observation = capacity(self.root, len(compressed))
        name = "cut-" + str(index).zfill(4) + ".json.gz"
        path = self.root / name
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(compressed)
            stream.flush()
            os.fsync(stream.fileno())
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                and info.st_uid == os.getuid() and info.st_size == len(compressed),
                "G5P_FRAME_POSTWRITE_CUSTODY_RED")
        self.bytes_written += len(compressed)
        self.frames.append({"cut_index": index, "path": name, "bytes": len(compressed),
                            "sha256": hashlib.sha256(compressed).hexdigest(),
                            "allocated_bytes": info.st_blocks * 512,
                            "capacity_before": observation})

    def finish(self, *, native_fin_closed):
        require(native_fin_closed is True and len(self.frames) == HORIZON_CUTS,
                "G5P_ALL1202_AND_NATIVE_FIN_REQUIRED")
        manifest = {"schema": SCHEMA, **self.binding, "actual_original_cuts": HORIZON_CUTS,
                    "frames": self.frames, "captured_bytes": self.bytes_written,
                    "qualification_claimed": False, "G5P_GREEN_claimed": False,
                    "real_orders_sent": 0}
        path = self.root / "manifest.json"
        body = wire(manifest)
        capacity(self.root, len(body))
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        return manifest


def verify_frames(manifest, *, read_frame, cuts, source_sha, source_tree,
                  owner_session, run_id, run_attempt, writer_binding_sha256):
    """Read all 1,202 authenticated ZIP members; reject incomplete originals."""
    expected = dict(source_sha=source_sha, source_tree=source_tree,
                    owner_session=owner_session, run_id=run_id, run_attempt=run_attempt,
                    writer_binding_sha256=writer_binding_sha256)
    require(type(manifest) is dict and manifest.get("schema") == SCHEMA
            and all(manifest.get(key) == value for key, value in expected.items())
            and type(manifest.get("frames")) is list
            and len(manifest["frames"]) == manifest.get("actual_original_cuts") == HORIZON_CUTS
            and type(cuts) is list and len(cuts) == HORIZON_CUTS
            and callable(read_frame) and manifest.get("G5P_GREEN_claimed") is False,
            "G5P_ALL1202_EXACT_ACTIONS_SOURCE_FRAMES_REQUIRED")
    hashes = set()
    total = 0
    for index, (reference, cut) in enumerate(zip(manifest["frames"], cuts, strict=True)):
        name = "cut-" + str(index).zfill(4) + ".json.gz"
        require(type(reference) is dict and reference.get("cut_index") == index
                and reference.get("path") == name
                and type(reference.get("bytes")) is int
                and 0 < reference["bytes"] <= MAX_FRAME_BYTES
                and HEX64.fullmatch(reference.get("sha256", "")),
                "G5P_FRAME_INDEX_OR_SIZE_REBOUND")
        compressed = read_frame(name)
        require(type(compressed) is bytes and len(compressed) == reference["bytes"]
                and hashlib.sha256(compressed).hexdigest() == reference["sha256"],
                "G5P_FRAME_ORIGINAL_ZIP_BYTES_MISMATCH")
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as source:
            raw = source.read(MAX_FRAME_BYTES * 8 + 1)
            require(len(raw) <= MAX_FRAME_BYTES * 8 and source.read(1) == b"",
                    "G5P_FRAME_EXPANSION_BOUND")
        frame = parse(raw)
        require(frame.get("schema") == FRAME_SCHEMA
                and all(frame.get(key) == value for key, value in expected.items())
                and frame.get("cut_index") == index and frame.get("as_of") == cut.get("as_of")
                and frame.get("receipt") == cut.get("native_receipt")
                and type(frame.get("originals")) is dict
                and set(frame["originals"]) == set(MEMBERS),
                "G5P_FRAME_SOURCE_RECEIPT_OR_FIVE_MEMBERS_REBOUND")
        for member, details in frame["originals"].items():
            require(type(details) is dict, "G5P_FRAME_MEMBER_INVALID")
            try:
                value = base64.b64decode(details["raw_base64"], validate=True)
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("G5P_FRAME_ORIGINAL_BASE64_INVALID") from error
            digest = hashlib.sha256(value).hexdigest()
            require(len(value) == details.get("bytes") == cut["member_bytes"][member]
                    and digest == details.get("sha256") == cut["original_member_sha256"][member],
                    "G5P_FRAME_FIVE_ORIGINAL_BYTE_HASHES_REBOUND")
            hashes.add(digest)
        total += len(compressed)
        require(total <= MAX_CAPTURE_BYTES, "G5P_FRAMES_AGGREGATE_BOUND")
    require(total == manifest.get("captured_bytes"), "G5P_FRAME_MANIFEST_TOTAL_REBOUND")
    obligations = original_prefix_obligations(cuts, available_payload_sha256=hashes)
    require_original_bytes_before_all1202_model(obligations)
    return {"verified_original_cuts": HORIZON_CUTS,
            "verified_original_payload_sha256_count": len(hashes),
            "original_prefix_sha256": obligations["original_prefix_sha256"],
            "raw_zip_frames_verified": True, "qualification_claimed": False}
