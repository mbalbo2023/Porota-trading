"""Bounded exact original-byte COPY/LITERAL storage, without custody authority.

Recovery decompresses persisted instructions and copies authenticated original
bytes. It never regenerates a gzip stream, SQLite image or financial payload.
The archive owner supplies lineage, expected hashes, admission and GC closure.
"""
from __future__ import annotations

from collections import defaultdict
import gzip
import hashlib
import re
import struct
import zlib

MAX_MEMBER_BYTES = 64 * 1024**2
MAX_PACK_BYTES = 128 * 1024**2
MAX_OPERATIONS = 524288
MAX_INDEX_ENTRIES = 262144
MAX_DEPENDENCY_DEPTH = 32
ANCHOR_BYTES = 24
MINIMUM_STRIDE = 64
MAX_COLLISIONS = 4
XOR_BLOCK_BYTES = 64 * 1024
MAGIC = b"RC6BIN1\x00"
HEADER = struct.Struct("!8sQII32s32s")
LITERAL = struct.Struct("!BI")
COPY = struct.Struct("!BII")
ZERO_HASH = b"\x00" * 32
HASH = re.compile(r"[0-9a-f]{64}\Z")
MAX_EXPANSION_BYTES = HEADER.size + MAX_MEMBER_BYTES + MAX_OPERATIONS * COPY.size


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _source(raw, name):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_MEMBER_BYTES:
        raise ValueError(name + "_BOUNDED_ORIGINAL_BYTES_REQUIRED")
    return raw


def _depth(value):
    if type(value) is not int or not 0 <= value <= MAX_DEPENDENCY_DEPTH:
        raise ValueError("BINARY_DEPENDENCY_DEPTH_INVALID")
    return value


def _hash(value, name):
    if not isinstance(value, str) or HASH.fullmatch(value) is None:
        raise ValueError(name + "_INVALID")
    return value


def _xor_equal_bytes(left, right):
    """Invert exact bytes in bounded C integer operations, never per-byte loops."""
    if len(left) != len(right):
        raise ValueError("BINARY_XOR_SOURCE_LENGTH_MISMATCH")
    output = bytearray(len(left))
    for cursor in range(0, len(left), XOR_BLOCK_BYTES):
        a, b = left[cursor:cursor + XOR_BLOCK_BYTES], right[cursor:cursor + XOR_BLOCK_BYTES]
        value = int.from_bytes(a, "little") ^ int.from_bytes(b, "little")
        output[cursor:cursor + len(a)] = value.to_bytes(len(a), "little")
    return bytes(output)


def _copy_operations(target, previous):
    stride = max(MINIMUM_STRIDE, (len(previous) + MAX_INDEX_ENTRIES - 1) // MAX_INDEX_ENTRIES)
    table = defaultdict(list)
    for offset in range(0, len(previous) - ANCHOR_BYTES + 1, stride):
        key = zlib.adler32(previous[offset:offset + ANCHOR_BYTES])
        if len(table[key]) < MAX_COLLISIONS:
            table[key].append(offset)
    cursor = literal = 0
    operations, a, b = [], None, None
    while cursor + ANCHOR_BYTES <= len(target):
        if a is None:
            key = zlib.adler32(target[cursor:cursor + ANCHOR_BYTES])
            a, b = key & 65535, key >> 16
        best_offset = best_size = 0
        for offset in table.get((b << 16) | a, ()):
            # An Adler collision is only a lookup hint, never byte authority.
            if previous[offset:offset + ANCHOR_BYTES] != target[cursor:cursor + ANCHOR_BYTES]:
                continue
            size = ANCHOR_BYTES
            maximum = min(len(previous) - offset, len(target) - cursor)
            while (size + 256 <= maximum
                   and previous[offset + size:offset + size + 256] == target[cursor + size:cursor + size + 256]):
                size += 256
            while size < maximum and previous[offset + size] == target[cursor + size]:
                size += 1
            if size > best_size:
                best_offset, best_size = offset, size
        if best_size:
            if literal < cursor:
                operations.append((0, target[literal:cursor]))
            operations.append((1, best_offset, best_size))
            if len(operations) > MAX_OPERATIONS:
                return None
            cursor += best_size
            literal, a, b = cursor, None, None
            continue
        if cursor + ANCHOR_BYTES == len(target):
            break
        outgoing, incoming = target[cursor], target[cursor + ANCHOR_BYTES]
        a = (a - outgoing + incoming) % 65521
        b = (b - ANCHOR_BYTES * outgoing + a - 1) % 65521
        cursor += 1
    if literal < len(target):
        operations.append((0, target[literal:]))
    return operations if len(operations) <= MAX_OPERATIONS else None


def _encode(target, operations, *, depth, previous_sha256):
    header = HEADER.pack(MAGIC, len(target), len(operations), depth,
                         bytes.fromhex(_sha(target)), bytes.fromhex(previous_sha256) if depth else ZERO_HASH)
    wire = header + b"".join(LITERAL.pack(op[0], len(op[1])) + op[1] if op[0] in {0, 2} else COPY.pack(*op)
                              for op in operations)
    if len(wire) > MAX_EXPANSION_BYTES:
        raise ValueError("BINARY_INSTRUCTION_CAPACITY_REACHED")
    packed = gzip.compress(wire, mtime=0, compresslevel=1)
    if len(packed) > MAX_PACK_BYTES:
        raise ValueError("BINARY_PACK_CAPACITY_REACHED")
    return packed


def encode_binary_pack(target_bytes, previous_bytes=None, *, previous_depth=0, force_full=False,
                       allow_xor=False):
    target = _source(target_bytes, "BINARY_TARGET")
    depth = _depth(previous_depth)
    if type(force_full) is not bool:
        raise ValueError("BINARY_FORCE_FULL_BOOLEAN_REQUIRED")
    if type(allow_xor) is not bool:
        raise ValueError("BINARY_ALLOW_XOR_BOOLEAN_REQUIRED")
    previous = None if previous_bytes is None else _source(previous_bytes, "BINARY_PREVIOUS")
    if previous is None and depth:
        raise ValueError("BINARY_PREVIOUS_DEPTH_UNEXPECTED")
    independent = _encode(target, [(0, target)], depth=0, previous_sha256=None)
    selected, selected_depth, copies, xor_bytes = independent, 0, 0, 0
    if previous is not None and not force_full and depth < MAX_DEPENDENCY_DEPTH and previous != target:
        operations = _copy_operations(target, previous)
        if operations is not None and any(op[0] == 1 for op in operations):
            dependent = _encode(target, operations, depth=depth + 1, previous_sha256=_sha(previous))
            if len(dependent) < len(independent):
                selected, selected_depth = dependent, depth + 1
                copies = sum(op[2] for op in operations if op[0] == 1)
        if allow_xor and len(previous) == len(target):
            delta = _xor_equal_bytes(target, previous)
            xor_pack = _encode(target, [(2, delta)], depth=depth + 1, previous_sha256=_sha(previous))
            if len(xor_pack) < len(selected):
                selected, selected_depth, copies, xor_bytes = xor_pack, depth + 1, 0, len(delta)
    return selected, {"pack_sha256": _sha(selected), "target_sha256": _sha(target),
        "target_bytes": len(target), "pack_bytes": len(selected), "dependency_depth": selected_depth,
        "previous_sha256": _sha(previous) if selected_depth else None,
        "copied_bytes": copies, "xor_bytes": xor_bytes, "independent_bytes": len(independent),
        "scope": "EXACT_ORIGINAL_BYTES_NO_CUSTODY_ACK_GC_AUTHORITY"}


def _inspect(pack, expected_pack_sha256, expected_target_sha256):
    expected_pack = _hash(expected_pack_sha256, "BINARY_EXPECTED_PACK_SHA256")
    expected_target = _hash(expected_target_sha256, "BINARY_EXPECTED_TARGET_SHA256")
    if type(pack) is not bytes or not 0 < len(pack) <= MAX_PACK_BYTES or _sha(pack) != expected_pack:
        raise ValueError("BINARY_PACK_WIRE_SHA256_OR_LENGTH_INVALID")
    decoder = zlib.decompressobj(wbits=31)
    try:
        wire = decoder.decompress(pack, MAX_EXPANSION_BYTES + 1)
    except zlib.error as error:
        raise ValueError("BINARY_PACK_STREAM_INVALID") from error
    if (len(wire) > MAX_EXPANSION_BYTES or not decoder.eof or decoder.unused_data
            or decoder.unconsumed_tail or len(wire) < HEADER.size):
        raise ValueError("BINARY_PACK_STREAM_INVALID")
    magic, length, count, depth, target_sha, previous_sha = HEADER.unpack_from(wire)
    if (magic != MAGIC or not 0 < length <= MAX_MEMBER_BYTES or not 0 < count <= MAX_OPERATIONS
            or not 0 <= depth <= MAX_DEPENDENCY_DEPTH or target_sha.hex() != expected_target
            or (depth == 0) != (previous_sha == ZERO_HASH) or previous_sha == target_sha):
        raise ValueError("BINARY_PACK_HEADER_INVALID")
    cursor, produced, copies = HEADER.size, 0, 0
    try:
        for _ in range(count):
            kind = wire[cursor]
            if kind == 0:
                _, size = LITERAL.unpack_from(wire, cursor)
                cursor += LITERAL.size
                if not size or cursor + size > len(wire):
                    raise ValueError("BINARY_LITERAL_INVALID")
                cursor += size
            elif kind == 1:
                _, offset, size = COPY.unpack_from(wire, cursor)
                cursor += COPY.size
                if not depth or not size or offset + size > MAX_MEMBER_BYTES:
                    raise ValueError("BINARY_COPY_INVALID")
                copies += 1
            elif kind == 2:
                _, size = LITERAL.unpack_from(wire, cursor)
                cursor += LITERAL.size
                if not depth or count != 1 or size != length or cursor + size > len(wire):
                    raise ValueError("BINARY_XOR_SHAPE_INVALID")
                cursor += size
                copies += 1
            else:
                raise ValueError("BINARY_OPCODE_UNSUPPORTED")
            produced += size
            if produced > length:
                raise ValueError("BINARY_MEMBER_EXPANSION_INVALID")
    except (IndexError, struct.error) as error:
        raise ValueError("BINARY_INSTRUCTION_TRUNCATED") from error
    if cursor != len(wire) or produced != length or bool(copies) != bool(depth):
        raise ValueError("BINARY_INSTRUCTION_CLOSURE_INVALID")
    return {"target_sha256": expected_target, "target_bytes": length, "dependency_depth": depth,
        "previous_sha256": previous_sha.hex() if depth else None,
        "verification_scope": "BINARY_PACK_WIRE_HASH_AND_TYPED_INSTRUCTIONS_ONLY"}, wire


def inspect_binary_pack(pack, *, expected_pack_sha256, expected_target_sha256):
    return _inspect(pack, expected_pack_sha256, expected_target_sha256)[0]


def decode_binary_pack(pack, *, expected_pack_sha256, expected_target_sha256,
                       previous_bytes=None, previous_depth=None):
    metadata, wire = _inspect(pack, expected_pack_sha256, expected_target_sha256)
    if metadata["dependency_depth"]:
        previous = _source(previous_bytes, "BINARY_PREVIOUS")
        if _sha(previous) != metadata["previous_sha256"] or _depth(previous_depth) + 1 != metadata["dependency_depth"]:
            raise ValueError("BINARY_PREVIOUS_HASH_OR_DEPTH_MISMATCH")
    else:
        if previous_bytes is not None or previous_depth is not None:
            raise ValueError("BINARY_INDEPENDENT_BASE_UNEXPECTED")
        previous = None
    cursor, output = HEADER.size, bytearray()
    count = HEADER.unpack_from(wire)[2]
    for _ in range(count):
        if wire[cursor] == 0:
            _, size = LITERAL.unpack_from(wire, cursor)
            cursor += LITERAL.size
            output.extend(wire[cursor:cursor + size])
            cursor += size
        elif wire[cursor] == 1:
            _, offset, size = COPY.unpack_from(wire, cursor)
            cursor += COPY.size
            if offset + size > len(previous):
                raise ValueError("BINARY_COPY_SOURCE_BOUNDS_INVALID")
            output.extend(previous[offset:offset + size])
        else:
            _, size = LITERAL.unpack_from(wire, cursor)
            cursor += LITERAL.size
            output.extend(_xor_equal_bytes(wire[cursor:cursor + size], previous))
            cursor += size
    source = bytes(output)
    if len(source) != metadata["target_bytes"] or _sha(source) != expected_target_sha256:
        raise ValueError("BINARY_RECONSTRUCTED_SOURCE_SHA256_MISMATCH")
    return source
