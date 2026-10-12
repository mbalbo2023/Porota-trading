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
import zlib

from rc6_shadow_runtime.archive_components import MEMBERS
from rc6_shadow_runtime.exact_binary_storage import (
    MAX_MEMBER_BYTES as MAX_BINARY_MEMBER_BYTES,
    MAX_PACK_BYTES as MAX_BINARY_PACK_BYTES,
    decode_binary_pack,
    encode_binary_pack,
)
from rc6_shadow_runtime.archive_physical_model import (
    HORIZON_CUTS, original_prefix_obligations,
    require_original_bytes_before_all1202_model,
)

SCHEMA = "rc6.g5p-paper-v3-original-frames.v2"
FRAME_SCHEMA = "rc6.g5p-paper-v3-original-five.v2"
MAX_FRAME_BYTES = 16 * 1024**2
MAX_MEMBER_BYTES = 128 * 1024**2
SQLITE_PAGE_BYTES = 4096
MATCH_BLOCK_BYTES = 4096
GZIP_MEMBERS = frozenset(("report.json.gz", "checkpoint.json.gz"))
MAX_GZIP_PARTS = 20_000
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


def _bounded_gzip(raw):
    """Replay each native packed-storage gzip member with its exact boundary."""
    require(type(raw) is bytes and len(raw) <= MAX_MEMBER_BYTES,
            "G5P_ORIGINAL_GZIP_MEMBER_BYTES_UNBOUNDED")
    offset, expanded, sizes, total = 0, [], [], 0
    while offset < len(raw):
        require(len(sizes) < MAX_GZIP_PARTS,
                "G5P_ORIGINAL_GZIP_PART_COUNT_UNBOUNDED")
        stream = zlib.decompressobj(wbits=31)
        start, chunks, size = offset, [], 0
        try:
            while not stream.eof:
                packet = raw[offset:offset+65536]
                require(bool(packet), "G5P_ORIGINAL_GZIP_MEMBER_INVALID")
                chunk = stream.decompress(packet, MAX_MEMBER_BYTES - total - size + 1)
                consumed = len(packet) - len(stream.unused_data) - len(stream.unconsumed_tail)
                require(consumed > 0 and not stream.unconsumed_tail,
                        "G5P_ORIGINAL_GZIP_EXPANSION_UNBOUNDED")
                offset += consumed
                size += len(chunk)
                require(total + size <= MAX_MEMBER_BYTES,
                        "G5P_ORIGINAL_GZIP_EXPANSION_UNBOUNDED")
                chunks.append(chunk)
            chunks.append(stream.flush())
            part = b"".join(chunks)
        except zlib.error as error:
            raise ValueError("G5P_ORIGINAL_GZIP_MEMBER_INVALID") from error
        require(offset > start and total + len(part) <= MAX_MEMBER_BYTES,
                "G5P_ORIGINAL_GZIP_EXPANSION_UNBOUNDED")
        require(gzip.compress(part, mtime=0, compresslevel=1)
                == raw[start:offset],
                "G5P_ORIGINAL_GZIP_REENCODE_NOT_BYTE_EXACT")
        expanded.append(part)
        sizes.append(len(part))
        total += len(part)
    require(bool(sizes), "G5P_ORIGINAL_GZIP_MEMBER_INVALID")
    return b"".join(expanded), sizes


def _edit(previous, current):
    common = min(len(previous), len(current))
    prefix = 0
    while (prefix + MATCH_BLOCK_BYTES <= common
           and previous[prefix:prefix+MATCH_BLOCK_BYTES]
           == current[prefix:prefix+MATCH_BLOCK_BYTES]):
        prefix += MATCH_BLOCK_BYTES
    while prefix < common and previous[prefix] == current[prefix]:
        prefix += 1
    suffix = 0
    while (suffix + MATCH_BLOCK_BYTES <= common - prefix
           and previous[len(previous)-suffix-MATCH_BLOCK_BYTES:len(previous)-suffix]
           == current[len(current)-suffix-MATCH_BLOCK_BYTES:len(current)-suffix]):
        suffix += MATCH_BLOCK_BYTES
    while suffix < common - prefix and previous[-suffix - 1] == current[-suffix - 1]:
        suffix += 1
    end = len(current) - suffix if suffix else len(current)
    return {"prefix_bytes": prefix, "suffix_bytes": suffix,
            "middle_base64": base64.b64encode(current[prefix:end]).decode("ascii")}


def _encode_member(name, raw, previous, previous_expanded=None):
    require(type(raw) is bytes and len(raw) <= MAX_MEMBER_BYTES,
            "G5P_ORIGINAL_MEMBER_BYTES_UNBOUNDED")
    base = previous or b""
    row = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
           "base_sha256": hashlib.sha256(base).hexdigest()}
    expanded = None
    if name in GZIP_MEMBERS:
        before = (previous_expanded if previous_expanded is not None else
                  _bounded_gzip(base)[0] if base else b"")
        current, parts = _bounded_gzip(raw)
        common = {"expanded_bytes": len(current),
                  "expanded_base_sha256": hashlib.sha256(before).hexdigest(),
                  "gzip_part_bytes": parts}
        edit = {**row, **common, "codec": "GZIP_LEVEL1_EXACT_EDIT_V1",
                **_edit(before, current)}
        row = edit
        # A single prefix/suffix edit can contain nearly the whole checkpoint
        # when several disjoint original regions change. The existing V3
        # exact-binary codec finds authenticated COPY runs in those bytes.
        # This is only a RAW transport alternative; the native V3 writer and
        # its archive/GC/dependency-depth policy remain untouched.
        if (before and current and len(edit["middle_base64"]) >= 65536
                and len(before) <= MAX_BINARY_MEMBER_BYTES
                and len(current) <= MAX_BINARY_MEMBER_BYTES):
            try:
                packed, info = encode_binary_pack(current, before, allow_xor=True)
            except ValueError as error:
                if str(error) not in {"BINARY_INSTRUCTION_CAPACITY_REACHED",
                                      "BINARY_PACK_CAPACITY_REACHED"}:
                    raise
            else:
                binary = {**{key: value for key, value in edit.items()
                             if key not in ("codec", "prefix_bytes", "suffix_bytes", "middle_base64")},
                          "codec": "GZIP_LEVEL1_EXACT_BINARY_PACK_V1",
                          "expanded_sha256": info["target_sha256"],
                          "binary_pack_sha256": info["pack_sha256"],
                          "binary_pack_depth": info["dependency_depth"],
                          "binary_pack_base64": base64.b64encode(packed).decode("ascii")}
                if len(wire(binary)) < len(wire(edit)):
                    row = binary
        expanded = current
    elif name == "projection.sqlite":
        changed = []
        for offset in range(0, len(raw), SQLITE_PAGE_BYTES):
            page = raw[offset:offset + SQLITE_PAGE_BYTES]
            if page != base[offset:offset + len(page)]:
                changed.append({"index": offset // SQLITE_PAGE_BYTES,
                                "raw_base64": base64.b64encode(page).decode("ascii")})
        row.update(codec="SQLITE_EXACT_PAGE_EDIT_V1", page_bytes=SQLITE_PAGE_BYTES,
                   changed_pages=changed)
    else:
        row.update(codec="EXACT_EDIT_V1", **_edit(base, raw))
    return row, expanded


def _apply_edit(base, details):
    prefix, suffix = details.get("prefix_bytes"), details.get("suffix_bytes")
    require(type(prefix) is int and type(suffix) is int and 0 <= prefix <= len(base)
            and 0 <= suffix <= len(base) - prefix,
            "G5P_FRAME_DELTA_BASE_RANGE_REBOUND")
    try:
        middle = base64.b64decode(details["middle_base64"], validate=True)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("G5P_FRAME_DELTA_BASE64_INVALID") from error
    require(len(middle) <= MAX_MEMBER_BYTES,
            "G5P_FRAME_DELTA_MIDDLE_UNBOUNDED")
    value = base[:prefix] + middle + (base[len(base)-suffix:] if suffix else b"")
    require(len(value) <= MAX_MEMBER_BYTES,
            "G5P_FRAME_DELTA_RESULT_UNBOUNDED")
    return value


def _decode_member(name, details, previous, previous_expanded=None):
    require(type(details) is dict and type(details.get("bytes")) is int
            and 0 <= details["bytes"] <= MAX_MEMBER_BYTES
            and HEX64.fullmatch(details.get("sha256", "")),
            "G5P_FRAME_MEMBER_INVALID")
    base = previous or b""
    require(details.get("base_sha256") == hashlib.sha256(base).hexdigest(),
            "G5P_FRAME_DELTA_PREVIOUS_ORIGINAL_REBOUND")
    expanded = None
    if name in GZIP_MEMBERS:
        require(details.get("codec") in ("GZIP_LEVEL1_EXACT_EDIT_V1",
                                          "GZIP_LEVEL1_EXACT_BINARY_PACK_V1"),
                "G5P_FRAME_GZIP_CODEC_REBOUND")
        before = (previous_expanded if previous_expanded is not None else
                  _bounded_gzip(base)[0] if base else b"")
        require(details.get("expanded_base_sha256") == hashlib.sha256(before).hexdigest(),
                "G5P_FRAME_GZIP_BASE_REBOUND")
        if details["codec"] == "GZIP_LEVEL1_EXACT_EDIT_V1":
            expanded = _apply_edit(before, details)
        else:
            require(type(details.get("expanded_sha256")) is str
                    and HEX64.fullmatch(details["expanded_sha256"])
                    and type(details.get("binary_pack_sha256")) is str
                    and HEX64.fullmatch(details["binary_pack_sha256"])
                    and type(details.get("binary_pack_depth")) is int
                    and details["binary_pack_depth"] in (0, 1),
                    "G5P_FRAME_BINARY_PACK_REFERENCE_INVALID")
            require(type(details.get("binary_pack_base64")) is str,
                    "G5P_FRAME_BINARY_PACK_BASE64_INVALID")
            try:
                packed = base64.b64decode(details["binary_pack_base64"], validate=True)
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("G5P_FRAME_BINARY_PACK_BASE64_INVALID") from error
            require(0 < len(packed) <= MAX_BINARY_PACK_BYTES,
                    "G5P_FRAME_BINARY_PACK_BOUND")
            depth = details["binary_pack_depth"]
            expanded = decode_binary_pack(packed,
                expected_pack_sha256=details["binary_pack_sha256"],
                expected_target_sha256=details["expanded_sha256"],
                previous_bytes=before if depth else None,
                previous_depth=0 if depth else None)
        require(details.get("expanded_bytes") == len(expanded),
                "G5P_FRAME_GZIP_EXPANDED_LENGTH_REBOUND")
        parts = details.get("gzip_part_bytes")
        require(type(parts) is list and 0 < len(parts) <= MAX_GZIP_PARTS
                and all(type(size) is int and 0 <= size <= MAX_MEMBER_BYTES for size in parts)
                and sum(parts) == len(expanded),
                "G5P_FRAME_GZIP_PART_BOUNDARY_REBOUND")
        offset, members = 0, []
        for size in parts:
            members.append(gzip.compress(expanded[offset:offset+size], mtime=0,
                                         compresslevel=1))
            offset += size
        value = b"".join(members)
    elif name == "projection.sqlite":
        require(details.get("codec") == "SQLITE_EXACT_PAGE_EDIT_V1"
                and details.get("page_bytes") == SQLITE_PAGE_BYTES
                and type(details.get("changed_pages")) is list
                and len(details["changed_pages"])
                    <= (details["bytes"] + SQLITE_PAGE_BYTES - 1)//SQLITE_PAGE_BYTES,
                "G5P_FRAME_SQLITE_PAGE_CODEC_REBOUND")
        size = details["bytes"]
        value = bytearray(base[:size])
        if len(value) < size:
            value.extend(b"\0" * (size - len(value)))
        last = -1
        for item in details["changed_pages"]:
            index = item.get("index") if type(item) is dict else None
            require(type(index) is int and last < index < (size + SQLITE_PAGE_BYTES - 1)//SQLITE_PAGE_BYTES,
                    "G5P_FRAME_SQLITE_PAGE_INDEX_REBOUND")
            last = index
            try:
                page = base64.b64decode(item["raw_base64"], validate=True)
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("G5P_FRAME_SQLITE_PAGE_BASE64_INVALID") from error
            offset = index * SQLITE_PAGE_BYTES
            require(len(page) == min(SQLITE_PAGE_BYTES, size-offset),
                    "G5P_FRAME_SQLITE_PAGE_LENGTH_REBOUND")
            value[offset:offset+len(page)] = page
        value = bytes(value)
    else:
        require(details.get("codec") == "EXACT_EDIT_V1",
                "G5P_FRAME_EDIT_CODEC_REBOUND")
        value = _apply_edit(base, details)
    require(len(value) == details["bytes"]
            and hashlib.sha256(value).hexdigest() == details["sha256"],
            "G5P_FRAME_RECONSTRUCTED_ORIGINAL_BYTES_REBOUND")
    return value, expanded


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
        self.previous = {}
        self.previous_expanded = {}

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
        encoded, current_expanded = {}, {}
        for name, value in sorted(originals.items()):
            encoded[name], expanded = _encode_member(
                name, value, self.previous.get(name), self.previous_expanded.get(name))
            if expanded is not None:
                current_expanded[name] = expanded
        frame = {"schema": FRAME_SCHEMA, **self.binding, "cut_index": index,
                 "as_of": as_of, "receipt": receipt,
                 "originals": encoded}
        compressed = gzip.compress(wire(frame), mtime=0)
        if not 0 < len(compressed) <= MAX_FRAME_BYTES:
            raise ValueError("G5P_ORIGINAL_FRAME_BYTES_EXCEED_LIMIT:cut=" + str(index)
                             + ":actual=" + str(len(compressed)) + ":limit=" + str(MAX_FRAME_BYTES))
        if self.bytes_written + len(compressed) > MAX_CAPTURE_BYTES:
            raise ValueError("G5P_ORIGINAL_CAPTURE_TOTAL_EXCEEDS_LIMIT:cut=" + str(index)
                             + ":actual=" + str(self.bytes_written + len(compressed))
                             + ":limit=" + str(MAX_CAPTURE_BYTES))
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
        self.previous = originals.copy()
        self.previous_expanded = current_expanded
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
    previous = {}
    previous_expanded = {}
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
        current, current_expanded = {}, {}
        for member, details in frame["originals"].items():
            value, expanded = _decode_member(member, details, previous.get(member),
                                             previous_expanded.get(member))
            digest = hashlib.sha256(value).hexdigest()
            require(len(value) == details.get("bytes") == cut["member_bytes"][member]
                    and digest == details.get("sha256") == cut["original_member_sha256"][member],
                    "G5P_FRAME_FIVE_ORIGINAL_BYTE_HASHES_REBOUND")
            hashes.add(digest)
            current[member] = value
            if expanded is not None:
                current_expanded[member] = expanded
        previous = current
        previous_expanded = current_expanded
        total += len(compressed)
        require(total <= MAX_CAPTURE_BYTES, "G5P_FRAMES_AGGREGATE_BOUND")
    require(total == manifest.get("captured_bytes"), "G5P_FRAME_MANIFEST_TOTAL_REBOUND")
    obligations = original_prefix_obligations(cuts, available_payload_sha256=hashes)
    require_original_bytes_before_all1202_model(obligations)
    return {"verified_original_cuts": HORIZON_CUTS,
            "verified_original_payload_sha256_count": len(hashes),
            "original_prefix_sha256": obligations["original_prefix_sha256"],
            "raw_zip_frames_verified": True, "qualification_claimed": False}
