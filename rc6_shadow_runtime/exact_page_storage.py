"""Pure bounded lossless page storage; no custody or ACK authority.

The owner supplies already captured source bytes and independent expected
hashes. Restore only decodes persisted data; it never invokes an encoder,
loads mutable tables, writes files, or discards a generation.
"""
from __future__ import annotations

import gzip
import hashlib
import re
import struct
import zlib

PAGE_SIZE = 4096
MAX_SOURCE_BYTES = 64 * 1024**2
MAX_PAGES = MAX_SOURCE_BYTES // PAGE_SIZE
MAX_ENCODED_PAGE_BYTES = PAGE_SIZE + 128
MAX_DEPENDENCY_DEPTH = 32
PAGE_CODECS = {"RAW_GZIP_V1": 1, "ZLIB_EXACT_PAGE_DELTA_V1": 2}
CODEC_NAMES = {value: key for key, value in PAGE_CODECS.items()}
PAGE_FIELDS = frozenset({"codec", "encoded", "target_sha256", "target_length", "base_sha256"})
MAGIC = b"RC6PGP1\x00"
HEADER = struct.Struct(">8sIQII32s32s")
INDEX = struct.Struct(">IBBH32s32s")
ZERO_HASH = b"\x00" * 32
MAX_PACK_BYTES = HEADER.size + MAX_PAGES * (INDEX.size + MAX_ENCODED_PAGE_BYTES)
HASH_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hash(value, name):
    if not isinstance(value, str) or HASH_PATTERN.fullmatch(value) is None:
        raise ValueError(name + "_INVALID")
    return value


def _page(value, name):
    if type(value) is not bytes or len(value) != PAGE_SIZE:
        raise ValueError(name + "_EXACT_4096_BYTES_REQUIRED")
    return value


def _source(value, name):
    if (type(value) is not bytes or not 0 < len(value) <= MAX_SOURCE_BYTES
            or len(value) % PAGE_SIZE):
        raise ValueError(name + "_BOUNDED_PAGED_BYTES_REQUIRED")
    return value


def _depth(value, name):
    if type(value) is not int or not 0 <= value <= MAX_DEPENDENCY_DEPTH:
        raise ValueError(name + "_INVALID")
    return value


def encode_page(target_bytes, base_original_bytes=None):
    """Encode one original page; choose a smaller exact dictionary delta.

    Gzip is deterministic for a given runtime. Recomposition never depends
    on that determinism: the encoded bytes themselves must be persisted.
    """
    target = _page(target_bytes, "PAGE_TARGET")
    base = None if base_original_bytes is None else _page(base_original_bytes, "PAGE_BASE")
    encoded = gzip.compress(target, mtime=0, compresslevel=1)
    codec, base_hash = "RAW_GZIP_V1", None
    if base is not None:
        encoder = zlib.compressobj(level=1, zdict=base)
        delta = encoder.compress(target) + encoder.flush()
        if len(delta) < len(encoded):
            encoded, codec, base_hash = delta, "ZLIB_EXACT_PAGE_DELTA_V1", _sha(base)
    if len(encoded) > MAX_ENCODED_PAGE_BYTES:
        raise ValueError("PAGE_ENCODED_CAPACITY_REACHED")
    return {"codec": codec, "encoded": encoded, "target_sha256": _sha(target),
            "target_length": PAGE_SIZE, "base_sha256": base_hash}


def decode_page(record, base_original_bytes=None):
    """Validate and decode one persisted record using its exact original base.

    This record is not authenticated by itself. The caller binds its bytes
    to the immutable pack receipt and the original source member hash.
    """
    if type(record) is not dict or set(record) != PAGE_FIELDS:
        raise ValueError("PAGE_RECORD_SHAPE_INVALID")
    codec, encoded = record["codec"], record["encoded"]
    if not isinstance(codec, str) or codec not in PAGE_CODECS:
        raise ValueError("PAGE_CODEC_UNSUPPORTED")
    if type(encoded) is not bytes or not 0 < len(encoded) <= MAX_ENCODED_PAGE_BYTES:
        raise ValueError("PAGE_ENCODED_LENGTH_INVALID")
    if type(record["target_length"]) is not int or record["target_length"] != PAGE_SIZE:
        raise ValueError("PAGE_TARGET_LENGTH_INVALID")
    target_hash = _hash(record["target_sha256"], "PAGE_TARGET_SHA256")
    if codec == "RAW_GZIP_V1":
        if record["base_sha256"] is not None or base_original_bytes is not None:
            raise ValueError("PAGE_INDEPENDENT_BASE_UNEXPECTED")
        decoder = zlib.decompressobj(wbits=31)
    else:
        base_hash = _hash(record["base_sha256"], "PAGE_BASE_SHA256")
        base = _page(base_original_bytes, "PAGE_BASE")
        if _sha(base) != base_hash:
            raise ValueError("PAGE_BASE_SHA256_MISMATCH")
        decoder = zlib.decompressobj(zdict=base)
    try:
        raw = decoder.decompress(encoded, PAGE_SIZE + 1)
    except zlib.error as error:
        raise ValueError("PAGE_COMPRESSED_STREAM_INVALID") from error
    if (len(raw) != PAGE_SIZE or not decoder.eof
            or decoder.unused_data or decoder.unconsumed_tail):
        raise ValueError("PAGE_STREAM_SHAPE_INVALID")
    if _sha(raw) != target_hash:
        raise ValueError("PAGE_TARGET_SHA256_MISMATCH")
    return raw


def encode_page_pack(target_bytes, previous_bytes=None, *, previous_depth=0, force_full=False):
    """Encode one file into one pack with a bounded previous-file dependency.

    The hard depth limit is 32. The archive owner can force an earlier full
    anchor (for example every 32 generations). No source generation is omitted.
    A byte-identical previous file gets an independent pack to avoid a
    self-reference if archives address previous files by their original SHA.
    """
    target = _source(target_bytes, "PACK_TARGET")
    previous = None if previous_bytes is None else _source(previous_bytes, "PACK_PREVIOUS")
    prior_depth = _depth(previous_depth, "PACK_PREVIOUS_DEPTH")
    if type(force_full) is not bool:
        raise ValueError("PACK_FORCE_FULL_INVALID")
    if previous is None and prior_depth:
        raise ValueError("PACK_PREVIOUS_DEPTH_WITHOUT_BASE")
    target_hash = _sha(target)
    previous_hash = _sha(previous) if previous is not None else None
    anchor_reason = None
    if previous is None:
        anchor_reason = "NO_PREVIOUS"
    elif force_full:
        anchor_reason = "EXPLICIT_FULL_ANCHOR"
    elif prior_depth == MAX_DEPENDENCY_DEPTH:
        anchor_reason = "MAXIMUM_DEPENDENCY_DEPTH"
    elif target_hash == previous_hash:
        anchor_reason = "IDENTICAL_FILE_INDEPENDENT_BASE"
    if anchor_reason:
        previous = None
    records, payloads, delta_pages = [], [], 0
    for ordinal, cursor in enumerate(range(0, len(target), PAGE_SIZE)):
        base = previous[cursor:cursor + PAGE_SIZE] if previous is not None else None
        if base is not None and len(base) != PAGE_SIZE:
            base = None
        record = encode_page(target[cursor:cursor + PAGE_SIZE], base)
        encoded = record["encoded"]
        delta_pages += int(record["codec"] == "ZLIB_EXACT_PAGE_DELTA_V1")
        records.append(INDEX.pack(ordinal, PAGE_CODECS[record["codec"]], 0, len(encoded),
                                  bytes.fromhex(record["target_sha256"]),
                                  bytes.fromhex(record["base_sha256"]) if record["base_sha256"] else ZERO_HASH))
        payloads.append(encoded)
    depth = prior_depth + 1 if delta_pages else 0
    base_hash = previous_hash if delta_pages else None
    header = HEADER.pack(MAGIC, PAGE_SIZE, len(target), len(records), depth,
                         bytes.fromhex(target_hash), bytes.fromhex(base_hash) if base_hash else ZERO_HASH)
    packed = header + b"".join(records) + b"".join(payloads)
    if len(packed) > MAX_PACK_BYTES:
        raise ValueError("PACK_ENCODED_CAPACITY_REACHED")
    if not delta_pages and anchor_reason is None:
        anchor_reason = "ALL_PAGES_INDEPENDENT"
    diagnostics = {"schema": "rc6.exact-page-pack-diagnostics.v1", "pack_sha256": _sha(packed),
        "target_sha256": target_hash, "target_bytes": len(target), "pack_bytes": len(packed),
        "previous_sha256": base_hash, "dependency_depth": depth,
        "page_count": len(records), "delta_pages": delta_pages,
        "full_pages": len(records) - delta_pages, "header_bytes": HEADER.size,
        "index_bytes": len(records) * INDEX.size, "encoded_page_bytes": sum(map(len, payloads)),
        "anchor_reason": anchor_reason,
        "scope": "BYTE_STORAGE_ONLY_NO_CUSTODY_ACK_GC_OR_ARCHIVE_CAPACITY_AUTHORITY"}
    return packed, diagnostics


def _inspect(pack, expected_pack_sha256, expected_target_sha256):
    pack_hash = _hash(expected_pack_sha256, "PACK_EXPECTED_SHA256")
    target_hash = _hash(expected_target_sha256, "PACK_EXPECTED_TARGET_SHA256")
    if type(pack) is not bytes or not HEADER.size <= len(pack) <= MAX_PACK_BYTES:
        raise ValueError("PACK_WIRE_LENGTH_INVALID")
    if _sha(pack) != pack_hash:
        raise ValueError("PACK_WIRE_SHA256_MISMATCH")
    magic, page_size, length, count, depth, source_sha, previous_sha = HEADER.unpack_from(pack)
    if (magic != MAGIC or page_size != PAGE_SIZE or not 0 < length <= MAX_SOURCE_BYTES
            or length % PAGE_SIZE or count != length // PAGE_SIZE or not 0 < count <= MAX_PAGES
            or not 0 <= depth <= MAX_DEPENDENCY_DEPTH):
        raise ValueError("PACK_HEADER_INVALID")
    if source_sha.hex() != target_hash:
        raise ValueError("PACK_TARGET_BINDING_MISMATCH")
    if (depth == 0) != (previous_sha == ZERO_HASH):
        raise ValueError("PACK_DEPENDENCY_HEADER_INVALID")
    if previous_sha == source_sha:
        raise ValueError("PACK_SELF_REFERENCE_FORBIDDEN")
    payload_cursor = HEADER.size + count * INDEX.size
    if payload_cursor > len(pack):
        raise ValueError("PACK_INDEX_TRUNCATED")
    records, delta_pages = [], 0
    for ordinal in range(count):
        number, codec, reserved, size, target_page_sha, base_page_sha = INDEX.unpack_from(pack, HEADER.size + ordinal * INDEX.size)
        if (number != ordinal or codec not in CODEC_NAMES or reserved
                or not 0 < size <= MAX_ENCODED_PAGE_BYTES or payload_cursor + size > len(pack)):
            raise ValueError("PACK_PAGE_INDEX_INVALID")
        is_delta = CODEC_NAMES[codec] == "ZLIB_EXACT_PAGE_DELTA_V1"
        if is_delta:
            if not depth or base_page_sha == ZERO_HASH:
                raise ValueError("PACK_PAGE_DEPENDENCY_INVALID")
            delta_pages += 1
        elif base_page_sha != ZERO_HASH:
            raise ValueError("PACK_INDEPENDENT_PAGE_BASE_INVALID")
        records.append((CODEC_NAMES[codec], payload_cursor, size, target_page_sha.hex(),
                        base_page_sha.hex() if is_delta else None))
        payload_cursor += size
    if payload_cursor != len(pack):
        raise ValueError("PACK_TRAILING_DATA_FORBIDDEN")
    if bool(delta_pages) != bool(depth):
        raise ValueError("PACK_DEPENDENCY_COUNT_INVALID")
    return {"target_sha256": target_hash, "target_bytes": length, "page_count": count,
            "dependency_depth": depth, "previous_sha256": previous_sha.hex() if depth else None,
            "delta_pages": delta_pages}, records


def inspect_page_pack(pack, *, expected_pack_sha256, expected_target_sha256):
    """Check wire/index binding, without claiming source reconstruction."""
    metadata, _ = _inspect(pack, expected_pack_sha256, expected_target_sha256)
    return {**metadata, "verification_scope": "PACK_WIRE_HASH_AND_TYPED_INDEX_ONLY"}


def decode_page_pack(pack, *, expected_pack_sha256, expected_target_sha256,
                     previous_bytes=None, previous_depth=None):
    """Restore the original file using only decoder operations.

    The archive owner resolves and verifies each preceding pack, holds its
    custody lock, and supplies that verified depth. This function does not
    resolve arbitrary paths, recursively load a chain, or authorize deletion.
    """
    metadata, records = _inspect(pack, expected_pack_sha256, expected_target_sha256)
    depth = metadata["dependency_depth"]
    if depth:
        previous = _source(previous_bytes, "PACK_PREVIOUS")
        if _sha(previous) != metadata["previous_sha256"]:
            raise ValueError("PACK_PREVIOUS_SHA256_MISMATCH")
        prior_depth = _depth(previous_depth, "PACK_PREVIOUS_DEPTH")
        if prior_depth + 1 != depth:
            raise ValueError("PACK_DEPENDENCY_DEPTH_MISMATCH")
    else:
        if previous_bytes is not None or previous_depth is not None:
            raise ValueError("PACK_INDEPENDENT_BASE_UNEXPECTED")
        previous = None
    output = bytearray()
    for ordinal, (codec, cursor, size, target_hash, base_hash) in enumerate(records):
        base = None
        if codec == "ZLIB_EXACT_PAGE_DELTA_V1":
            base = previous[ordinal * PAGE_SIZE:(ordinal + 1) * PAGE_SIZE]
        record = {"codec": codec, "encoded": pack[cursor:cursor + size], "target_sha256": target_hash,
                  "target_length": PAGE_SIZE, "base_sha256": base_hash}
        output.extend(decode_page(record, base))
    result = bytes(output)
    if len(result) != metadata["target_bytes"] or _sha(result) != expected_target_sha256:
        raise ValueError("PACK_RECONSTRUCTED_SOURCE_SHA256_MISMATCH")
    return result
