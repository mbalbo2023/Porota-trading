"""Exact original component archive; recipes confer no trading authority.

Immutable pack headers are lookup hints. Before reuse or restoration the whole
pack, its original component CID, and every original source-member SHA are
verified. There is no mutable CAS database and restore never calls an encoder.
The owner supplies its archive/writer locks, quota admission and receipt chain.
"""
import base64
import gzip
import hashlib
from itertools import product
import json
import os
from pathlib import Path
import re
import stat
import struct
import uuid
import zlib

from .exact_page_storage import encode_page_pack, decode_page_pack, inspect_page_pack
from .exact_binary_storage import encode_binary_pack, decode_binary_pack, inspect_binary_pack
from . import exact_page_storage as page_storage


RECIPE_SCHEMA = "RC6_SHADOW_ARCHIVE_COMPONENT_RECIPE_V3"
SLICE_RECIPE_SCHEMA = "RC6_SHADOW_ARCHIVE_COMPONENT_RECIPE_V4"
ACK_SCHEMA = "RC6_SHADOW_ARCHIVE_ACK_V3"
ARCHIVER_ID = "RC6_LOCAL_PRIVATE_COMPONENT_ARCHIVER_V3"
PACK_MAGIC = b"RC6CASP3"
PACK_HEADER = struct.Struct("!8sI")
PACK_RECORD = struct.Struct("!32sII")
COMPONENT_RECORD = struct.Struct("!II32s")
SLICE_COMPONENT_RECORD = struct.Struct("!IIII32s")
SLICE_ARRAY_CODEC = "GZIP_BIG_ENDIAN_BINARY_SLICE_INDEX_V1"
MAX_COMPONENTS = 524288
MAX_PACK_BYTES = 128 * 1024**2
MAX_RECIPE_BYTES = 64 * 1024**2
MAX_MEMBER_BYTES = 64 * 1024**2
MAX_REFS = 1048576
PACK_NAME = re.compile(r"([0-9a-f]{64})\.cas\.pack\Z")
ID = re.compile(r"[0-9a-f]{32}\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")
BASE_MEMBERS = frozenset({"manifest.json", "report.json.gz", "checkpoint.json.gz", "status.json"})
MEMBERS = BASE_MEMBERS | {"projection.sqlite"}
LEVEL = "ALL_ORIGINAL_MEMBER_BYTES_MANIFEST_CRC_AND_ROLE_WIRE"
DEPENDENT_RECOVERIES = frozenset({"EXACT_PAGE_PACK", "EXACT_BINARY_PACK"})
MAX_WIRE_REGION_GROUPS = 16
MIN_WIRE_GROUP_PAGES = 64
MAX_GROUPED_WIRE_COMPONENTS = 1 + 2 * MAX_WIRE_REGION_GROUPS
MAX_WIRE_SLICES = 128
MIN_WIRE_SLICE_BYTES = 4096
_WIRE_BOUNDARY = re.compile(b"[\x00-\x0f]\x00")


class ComponentCatalog(dict):
    """Lookup hints plus the count of ALL physical records, including duplicates."""
    def __init__(self, *args, physical_records=0, **kwargs):
        super().__init__(*args, **kwargs)
        self.physical_records = physical_records


def _exact_wire_ranges(raw):
    """Boundaries without copying a verified, immutable parent image.

    A C regex search avoids a Python rolling-hash pass over every byte. The
    minimum length makes <=128 references a structural bound. A maximum span
    terminates the search even for adversarial bytes with no boundary. Changed
    or shifted wire is still exact concatenation, never a semantic approximation.
    """
    immutable_view = (type(raw) is memoryview and type(raw.obj) is bytes
                      and raw.readonly and raw.c_contiguous
                      and raw.format == "B" and raw.ndim == 1)
    if (type(raw) is not bytes and not immutable_view) or not raw or len(raw) > MAX_PACK_BYTES:
        raise ValueError("RETENTION_SLICE_BOUNDED_ORIGINAL_WIRE_REQUIRED")
    minimum = max(MIN_WIRE_SLICE_BYTES, (len(raw) + MAX_WIRE_SLICES - 1) // MAX_WIRE_SLICES)
    cursor, result = 0, []
    while cursor < len(raw):
        start = min(len(raw), cursor + minimum)
        stop = min(len(raw), cursor + 4 * minimum)
        match = _WIRE_BOUNDARY.search(raw, start, stop)
        end = match.end() if match is not None else stop
        result.append((cursor, end))
        cursor = end
    if len(result) > MAX_WIRE_SLICES or cursor != len(raw):
        raise ValueError("RETENTION_SLICE_EXACT_BOUND_INVALID")
    return tuple(result)


def exact_wire_slices(raw):
    """Return exact pieces for a proposal; range indexing itself makes no copy."""
    if type(raw) is not bytes:
        raise ValueError("RETENTION_SLICE_BOUNDED_ORIGINAL_WIRE_REQUIRED")
    return tuple(raw[start:end] for start, end in _exact_wire_ranges(raw))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def grouped_wire_parts(raw, recovery, *, expected_pack_sha256, expected_target_sha256):
    """Factor existing wire into at most 33 exact concatenation components.

    This is a CAS layout proposal, never a codec or a capacity certificate.
    The PAGE header changes independently of groups of index rows and groups
    of original compressed page payloads. Boundaries use whole format records,
    and a power-of-two group size limits both index and payload to 16 groups.
    Small inputs still group at least 64 pages; a page cannot create an unbounded
    component. Dictionary IDs, compressed streams and CRC bytes are untouched.

    BIN is a single gzip stream. It has no independently addressable compressed
    instruction regions; keep its exact original wire as one component.
    """
    if recovery == "EXACT_BINARY_PACK":
        inspect_binary_pack(raw, expected_pack_sha256=expected_pack_sha256,
                            expected_target_sha256=expected_target_sha256)
        return (raw,)
    if recovery != "EXACT_PAGE_PACK":
        raise ValueError("RETENTION_WIRE_FACTOR_RECOVERY_UNSUPPORTED")
    metadata = inspect_page_pack(raw, expected_pack_sha256=expected_pack_sha256,
                                 expected_target_sha256=expected_target_sha256)
    count = metadata["page_count"]
    minimum = (count + MAX_WIRE_REGION_GROUPS - 1) // MAX_WIRE_REGION_GROUPS
    group_pages = max(MIN_WIRE_GROUP_PAGES, 1 << (minimum - 1).bit_length())
    index_start = page_storage.HEADER.size
    payload_start = index_start + count * page_storage.INDEX.size
    indices, payloads, cursor = [], [], payload_start
    for start in range(0, count, group_pages):
        end = min(count, start + group_pages)
        index = raw[index_start + start * page_storage.INDEX.size:
                    index_start + end * page_storage.INDEX.size]
        length = sum(row[3] for row in page_storage.INDEX.iter_unpack(index))
        indices.append(index)
        payloads.append(raw[cursor:cursor + length])
        cursor += length
    parts = (raw[:index_start], *indices, *payloads)
    if (cursor != len(raw) or not 0 < len(parts) <= MAX_GROUPED_WIRE_COMPONENTS
            or any(not part for part in parts) or b"".join(parts) != raw):
        raise ValueError("RETENTION_WIRE_FACTOR_EXACT_BOUND_INVALID")
    return parts


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def loads(raw):
    def pairs(rows):
        value = {}
        for key, member in rows:
            if key in value:
                raise ValueError("RETENTION_RECIPE_DUPLICATE_KEY")
            value[key] = member
        return value
    def constant(_):
        raise ValueError("RETENTION_RECIPE_NONFINITE")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def integer(value, maximum, *, positive=False):
    if type(value) is not int or not int(positive) <= value <= maximum:
        raise ValueError("RETENTION_RECIPE_TYPED_INDEX_INVALID")
    return value


def _metadata(info, *, directory=False):
    if (not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
            or not directory and info.st_nlink != 1):
        raise ValueError("RETENTION_COMPONENT_CUSTODY_INVALID")
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid, info.st_nlink,
            info.st_size, info.st_blocks, info.st_mtime_ns, info.st_ctime_ns)


def read(path, *, maximum, header_only=None):
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("RETENTION_COMPONENT_ALIAS_FORBIDDEN")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NOATIME", 0))
    try:
        before = _metadata(os.fstat(fd))
        if before[6] > maximum:
            raise ValueError("RETENTION_COMPONENT_CAPACITY_REACHED")
        limit = before[6] if header_only is None else min(before[6], header_only)
        chunks, size = [], 0
        while size < limit:
            raw = os.read(fd, min(65536, limit-size))
            if not raw:
                raise ValueError("RETENTION_COMPONENT_CHANGED")
            chunks.append(raw); size += len(raw)
        if _metadata(os.fstat(fd)) != before or _metadata(path.lstat()) != before:
            raise ValueError("RETENTION_COMPONENT_CHANGED")
        return b"".join(chunks), before
    finally:
        os.close(fd)


def inflate(raw, *, maximum):
    """One portable gzip stream; CRC, exact EOF and no trailing bytes."""
    stream = zlib.decompressobj(wbits=31)
    try:
        decoded = stream.decompress(raw, maximum+1)
    except zlib.error as error:
        raise ValueError("RETENTION_COMPONENT_GZIP_INVALID") from error
    if len(decoded) > maximum or not stream.eof or stream.unused_data or stream.unconsumed_tail:
        raise ValueError("RETENTION_COMPONENT_GZIP_INVALID")
    return decoded


def source_gzip_frames(raw):
    """Partition the ORIGINAL compressed source, validating every gzip CRC."""
    result, cursor, expanded = [], 0, 0
    while cursor < len(raw):
        start, stream = cursor, zlib.decompressobj(wbits=31)
        while not stream.eof:
            part = raw[cursor:cursor+65536]
            if not part:
                raise ValueError("RETENTION_SOURCE_GZIP_INVALID")
            try:
                plain = stream.decompress(part, MAX_MEMBER_BYTES-expanded+1)
            except zlib.error as error:
                raise ValueError("RETENTION_SOURCE_GZIP_INVALID") from error
            expanded += len(plain)
            if expanded > MAX_MEMBER_BYTES or stream.unconsumed_tail:
                raise ValueError("RETENTION_SOURCE_GZIP_CAPACITY_REACHED")
            cursor += len(part)-len(stream.unused_data)
        if cursor <= start:
            raise ValueError("RETENTION_SOURCE_GZIP_INVALID")
        result.append(raw[start:cursor])
        if len(result) > MAX_REFS:
            raise ValueError("RETENTION_COMPONENT_INDEX_CAPACITY_REACHED")
    if not result:
        raise ValueError("RETENTION_SOURCE_GZIP_INVALID")
    return tuple(result)


def _array(raw, *, count, codec="GZIP_BIG_ENDIAN_BINARY_INDEX_V1"):
    encoded = gzip.compress(raw, mtime=0, compresslevel=1)
    return {"codec": codec, "count": count,
        "sha256": sha(encoded), "payload": base64.b64encode(encoded).decode("ascii")}


def _decode_array(record, *, width, maximum, codec="GZIP_BIG_ENDIAN_BINARY_INDEX_V1"):
    if (not isinstance(record, dict) or set(record) != {"codec", "count", "sha256", "payload"}
            or record["codec"] != codec
            or not isinstance(record["sha256"], str) or HEX.fullmatch(record["sha256"]) is None
            or not isinstance(record["payload"], str)):
        raise ValueError("RETENTION_RECIPE_ARRAY_INVALID")
    count = integer(record["count"], maximum)
    if len(record["payload"]) > MAX_RECIPE_BYTES:
        raise ValueError("RETENTION_RECIPE_CAPACITY_REACHED")
    try:
        ascii_payload = record["payload"].encode("ascii")
        compressed = base64.b64decode(ascii_payload, validate=True)
    except (ValueError, UnicodeError) as error:
        raise ValueError("RETENTION_RECIPE_ARRAY_INVALID") from error
    if base64.b64encode(compressed) != ascii_payload or sha(compressed) != record["sha256"]:
        raise ValueError("RETENTION_RECIPE_ARRAY_INVALID")
    raw = inflate(compressed, maximum=count*width)
    if len(raw) != count*width:
        raise ValueError("RETENTION_RECIPE_ARRAY_INVALID")
    return raw, count


def _pack_index(raw, size):
    if len(raw) < PACK_HEADER.size:
        raise ValueError("RETENTION_COMPONENT_PACK_INVALID")
    magic, count = PACK_HEADER.unpack_from(raw)
    if magic != PACK_MAGIC or not 0 < count <= MAX_COMPONENTS:
        raise ValueError("RETENTION_COMPONENT_PACK_INVALID")
    cursor = PACK_HEADER.size + count * PACK_RECORD.size
    if len(raw) < cursor or cursor > size:
        raise ValueError("RETENTION_COMPONENT_PACK_INVALID")
    rows, used = [], set()
    for cid, offset, length in PACK_RECORD.iter_unpack(raw[PACK_HEADER.size:cursor]):
        if cid in used or offset != cursor or not 0 < length <= MAX_PACK_BYTES or cursor+length > size:
            raise ValueError("RETENTION_COMPONENT_PACK_INVALID")
        used.add(cid); rows.append((cid.hex(), offset, length)); cursor += length
    if cursor != size:
        raise ValueError("RETENTION_COMPONENT_PACK_INVALID")
    return tuple(rows)


class ComponentArchive:
    """Call-scoped exact-byte storage, operated under the owner's archive lock."""
    def __init__(self, owner):
        self.owner, self.root = owner, owner.archive_root
        self.packs, self.recipes, self.projections = {}, {}, {}

    def _binary_option(self, raw, previous, previous_depth, force_full, *, allow_xor=False):
        """One bounded proposal; never a runtime flag or an admission bypass."""
        return encode_binary_pack(raw, previous, previous_depth=previous_depth,
                                  force_full=force_full, allow_xor=allow_xor)

    def _common_dependency_depth(self, receipt):
        """Conservative anchor bound from hash-authenticated recipe metadata.

        This grants no payload/ACK/GC proof. Restoration and GC separately
        reopen and authenticate the physical components. A common archive
        chain can outlive either member's individual codec chain, so every
        encoding proposal must anchor together before another edge reaches33.
        """
        from rc6_dynamic_universe.common import digest
        depth, visited, current = 0, set(), receipt
        while True:
            recipe = self._recipe(current)
            ident = recipe["generation_id"]
            if ident in visited:
                raise ValueError("RETENTION_PAGE_DEPENDENCY_CYCLE_OR_DEPTH")
            visited.add(ident)
            bases = {}
            for name in ("projection.sqlite", "checkpoint.json.gz"):
                record = recipe["members"].get(name)
                if record is None:
                    continue
                accepted = ({"EXACT_PAGE_PACK", "EXACT_BINARY_PACK"} if name == "projection.sqlite"
                            else {"SOURCE_GZIP_FRAME_CONCAT", "EXACT_BINARY_PACK"})
                if not isinstance(record, dict) or record.get("recovery") not in accepted:
                    raise ValueError("RETENTION_RECIPE_RECOVERY_CODEC_INVALID")
                member_depth = integer(record.get("dependency_depth", 0), 32)
                if not member_depth:
                    if any(record.get(key) is not None for key in ("base_generation_id", "base_receipt_digest")):
                        raise ValueError("RETENTION_PAGE_INDEPENDENT_BASE_UNEXPECTED")
                    continue
                base, proof = record.get("base_generation_id"), record.get("base_receipt_digest")
                if not isinstance(base, str) or ID.fullmatch(base) is None or not isinstance(proof, str) or HEX.fullmatch(proof) is None:
                    raise ValueError("RETENTION_PAGE_DEPENDENCY_INVALID")
                if base in bases and bases[base] != proof:
                    raise ValueError("RETENTION_PAGE_DEPENDENCY_LINEAGE_INVALID")
                bases[base] = proof
            if not bases:
                return depth
            if len(bases) != 1 or depth >= 32:
                raise ValueError("RETENTION_PAGE_DEPENDENCY_CYCLE_OR_DEPTH")
            base, expected = next(iter(bases.items()))
            prior, _ = self.owner._read_control(self.root / (base + ".receipt.json"))
            if (digest(prior) != expected or type(prior.get("receipt_sequence")) is not int
                    or prior["receipt_sequence"] >= current["receipt_sequence"]):
                raise ValueError("RETENTION_PAGE_DEPENDENCY_LINEAGE_INVALID")
            current, depth = prior, depth + 1

    def _pack(self, name):
        match = PACK_NAME.fullmatch(name) if isinstance(name, str) else None
        if match is None:
            raise ValueError("RETENTION_COMPONENT_PACK_PATH_INVALID")
        if name not in self.packs:
            # A proof context may traverse 33 generations. Retain at most one
            # original pack, rather than the entire transitive archive in RAM.
            # Every evicted pack is reopened and authenticated if needed again.
            self.packs.clear()
            raw, _ = read(self.root / name, maximum=MAX_PACK_BYTES)
            if sha(raw) != match[1]:
                raise ValueError("RETENTION_COMPONENT_PACK_HASH_MISMATCH")
            self.packs[name] = raw, _pack_index(raw, len(raw))
        return self.packs[name]

    def _catalog(self):
        """Headers locate candidate bytes; they never authenticate a component."""
        catalog, count = ComponentCatalog(), 0
        for path in sorted(path for path in self.owner._read_only_paths(self.root)
                           if path.name.endswith(".cas.pack")):
            header, info = read(path, maximum=MAX_PACK_BYTES, header_only=PACK_HEADER.size)
            if len(header) != PACK_HEADER.size:
                raise ValueError("RETENTION_COMPONENT_PACK_INVALID")
            magic, number = PACK_HEADER.unpack(header)
            if magic != PACK_MAGIC or not 0 < number <= MAX_COMPONENTS-count:
                raise ValueError("RETENTION_COMPONENT_INDEX_CAPACITY_REACHED")
            indexed, same = read(path, maximum=MAX_PACK_BYTES, header_only=PACK_HEADER.size+number*PACK_RECORD.size)
            if same != info:
                raise ValueError("RETENTION_COMPONENT_CHANGED")
            rows = _pack_index(indexed, info[6]); count += len(rows)
            for ordinal, (cid, _, _) in enumerate(rows):
                catalog.setdefault(cid, (path.name, ordinal))
        catalog.physical_records = count
        return catalog

    def _component(self, cid, location):
        return self._components(((cid, location),))[cid]

    def _components(self, entries, *, byte_budget=MAX_PACK_BYTES, multiplicities=None, visitor=None):
        self.packs.clear()
        try:
            return self._verified_components(entries, byte_budget=byte_budget,
                                             multiplicities=multiplicities, visitor=visitor)
        finally:
            # An exception must not leave a proof available to the next read.
            self.packs.clear()

    def _verified_components(self, entries, *, byte_budget, multiplicities, visitor):
        """Authenticate each whole parent once, with a one-pack byte cache."""
        groups, result, total = {}, {}, 0
        for cid, location in entries:
            if not isinstance(cid, str) or HEX.fullmatch(cid) is None:
                raise ValueError("RETENTION_COMPONENT_CID_INVALID")
            if type(location) is not tuple or len(location) not in (2, 4):
                raise ValueError("RETENTION_COMPONENT_BINDING_MISMATCH")
            groups.setdefault(location[0], {}).setdefault(location[1], []).append((cid, location))
        for name, parents in groups.items():
            raw, rows = self._pack(name)
            # Check every request against authenticated parent lengths BEFORE
            # materializing any ranges from this pack. Repeated references
            # count repeatedly toward the member's expansion, not just once.
            for ordinal, requests in parents.items():
                integer(ordinal, MAX_COMPONENTS)
                if ordinal >= len(rows):
                    raise ValueError("RETENTION_COMPONENT_BINDING_MISMATCH")
                _, _, size = rows[ordinal]
                for cid, location in requests:
                    if len(location) == 4:
                        start, length = location[2:]
                        integer(start, MAX_PACK_BYTES)
                        integer(length, MAX_PACK_BYTES, positive=True)
                        if start + length > size:
                            raise ValueError("RETENTION_COMPONENT_SLICE_BOUNDS_INVALID")
                    else:
                        length = size
                    total += length * (multiplicities.get(cid, 1) if multiplicities is not None else 1)
                    if total > byte_budget:
                        raise ValueError("RETENTION_RECIPE_MEMBER_EXPANSION_INVALID")
            for ordinal, requests in parents.items():
                integer(ordinal, MAX_COMPONENTS)
                if ordinal >= len(rows):
                    raise ValueError("RETENTION_COMPONENT_BINDING_MISMATCH")
                parent_cid, offset, size = rows[ordinal]
                parent = memoryview(raw)[offset:offset + size]
                if sha(parent) != parent_cid:
                    raise ValueError("RETENTION_COMPONENT_HASH_MISMATCH")
                for cid, location in requests:
                    if len(location) == 2:
                        if parent_cid != cid:
                            raise ValueError("RETENTION_COMPONENT_BINDING_MISMATCH")
                        part = parent
                    else:
                        start, length = location[2:]
                        integer(start, MAX_PACK_BYTES)
                        integer(length, MAX_PACK_BYTES, positive=True)
                        if start + length > size:
                            raise ValueError("RETENTION_COMPONENT_SLICE_BOUNDS_INVALID")
                        part = parent[start:start + length]
                    if sha(part) != cid:
                        raise ValueError("RETENTION_COMPONENT_HASH_MISMATCH")
                    if visitor is None:
                        result[cid] = bytes(part)
                    else:
                        visitor(cid, location, part)
                    del part
                del parent
            self.packs.clear()
            del raw, rows
        return result

    def _slice_catalog(self, catalog, recipe):
        """Call-scoped hints from the preceding authenticated recipe only.

        The original whole pack and its parent CID are reauthenticated before
        deriving each slice. Whole packs stay referenced by the V4 recipe and
        GC never treats a referenced range as permission to delete its parent.
        No persistent mutable index or proof cache is introduced.
        """
        result = ComponentCatalog(catalog, physical_records=getattr(catalog, "physical_records", len(catalog)))
        entries = self._indices(recipe)
        def remember(cid, location, view):
            parent_start = location[2] if len(location) == 4 else 0
            for start, end in _exact_wire_ranges(view):
                result.setdefault(sha(view[start:end]),
                                  (location[0], location[1], parent_start + start, end - start))
            if len(result) > MAX_COMPONENTS:
                raise ValueError("RETENTION_COMPONENT_INDEX_CAPACITY_REACHED")
        # A recipe cannot declare more wire than all five original bounded
        # members and their existing encodings. Streaming retains one parent,
        # one range and small aliases; never every historical source image.
        self._components(entries, byte_budget=MAX_PACK_BYTES * len(MEMBERS), visitor=remember)
        return result

    def _publish(self, path, raw, *, prefix):
        temporary = self.root / (prefix + uuid.uuid4().hex + ".tmp")
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            self.owner._fault("archive_before_publish_object")
            if path.exists() or path.is_symlink():
                previous, _ = read(path, maximum=max(MAX_PACK_BYTES, MAX_RECIPE_BYTES))
                if previous != raw:
                    raise ValueError("RETENTION_COMPONENT_OBJECT_CONFLICT")
                temporary.unlink()
            else:
                temporary.rename(path)
            self.owner._sync_directory(self.root)
        finally:
            temporary.unlink(missing_ok=True)

    def _recipe(self, receipt):
        ident = receipt.get("generation_id")
        if (not isinstance(ident, str) or ID.fullmatch(ident) is None or receipt.get("schema") != ACK_SCHEMA
                or receipt.get("archiver_id") != ARCHIVER_ID
                or receipt.get("archive_uri") != "local-private://" + ident + ".recipe.gz"
                or not isinstance(receipt.get("archive_sha256"), str) or HEX.fullmatch(receipt["archive_sha256"]) is None):
            raise ValueError("RETENTION_COMPONENT_RECEIPT_INVALID")
        if ident not in self.recipes:
            raw, _ = read(self.root / (ident + ".recipe.gz"), maximum=MAX_RECIPE_BYTES)
            if sha(raw) != receipt["archive_sha256"]:
                raise ValueError("RETENTION_ARCHIVE_OBJECT_HASH_MISMATCH")
            value = loads(inflate(raw, maximum=MAX_RECIPE_BYTES))
            if (not isinstance(value, dict) or set(value) != {"schema", "generation_id", "sequence", "manifest_sha256",
                    "members", "packs", "components", "manifest_links", "verification_level", "custody"}
                    or value.get("schema") not in {RECIPE_SCHEMA, SLICE_RECIPE_SCHEMA} or value.get("generation_id") != ident
                    or value.get("manifest_sha256") != receipt.get("manifest_sha256")
                    or type(value.get("sequence")) is not int or value["sequence"] != receipt.get("sequence")
                    or value.get("verification_level") != LEVEL or value.get("custody") != "LOCAL_PRIVATE_FSYNC_NOT_WORM"
                    or not isinstance(value.get("members"), dict) or set(value["members"]) not in (BASE_MEMBERS, MEMBERS)):
                raise ValueError("RETENTION_RECIPE_HEADER_INVALID")
            self.recipes[ident] = value
        return self.recipes[ident]

    def _indices(self, recipe):
        packs = recipe.get("packs")
        if (not isinstance(packs, list) or not 0 < len(packs) <= MAX_COMPONENTS
                or any(not isinstance(name, str) or PACK_NAME.fullmatch(name) is None for name in packs)
                or len(set(packs)) != len(packs)):
            raise ValueError("RETENTION_RECIPE_PACK_INDEX_INVALID")
        sliced = recipe.get("schema") == SLICE_RECIPE_SCHEMA
        if recipe.get("schema") not in {RECIPE_SCHEMA, SLICE_RECIPE_SCHEMA}:
            raise ValueError("RETENTION_RECIPE_HEADER_INVALID")
        shape = SLICE_COMPONENT_RECORD if sliced else COMPONENT_RECORD
        raw, count = _decode_array(recipe.get("components"), width=shape.size, maximum=MAX_COMPONENTS,
            codec=SLICE_ARRAY_CODEC if sliced else "GZIP_BIG_ENDIAN_BINARY_INDEX_V1")
        if not count:
            raise ValueError("RETENTION_RECIPE_COMPONENT_INDEX_INVALID")
        rows, used_packs, seen = [], set(), set()
        for row in shape.iter_unpack(raw):
            pack, ordinal, cid = row[0], row[1], row[-1]
            if pack >= len(packs) or ordinal >= MAX_COMPONENTS or cid in seen:
                raise ValueError("RETENTION_RECIPE_COMPONENT_INDEX_INVALID")
            location = (packs[pack], ordinal)
            if sliced:
                start, length = row[2:4]
                integer(start, MAX_PACK_BYTES)
                integer(length, MAX_PACK_BYTES, positive=True)
                if start + length > MAX_PACK_BYTES:
                    raise ValueError("RETENTION_COMPONENT_SLICE_BOUNDS_INVALID")
                location += (start, length)
            seen.add(cid); used_packs.add(pack); rows.append((cid.hex(), location))
        if used_packs != set(range(len(packs))):
            raise ValueError("RETENTION_RECIPE_UNUSED_PACK")
        return rows

    def _member(self, record, components, used, *, decode=True):
        expected = {"sha256", "bytes", "recovery", "references"}
        if isinstance(record, dict) and record.get("recovery") in DEPENDENT_RECOVERIES:
            expected |= {"pack_sha256", "dependency_depth", "base_generation_id", "base_receipt_digest"}
        if (not isinstance(record, dict) or set(record) != expected
                or not isinstance(record.get("sha256"), str) or HEX.fullmatch(record["sha256"]) is None):
            raise ValueError("RETENTION_RECIPE_MEMBER_INVALID")
        size = integer(record.get("bytes"), MAX_MEMBER_BYTES, positive=True)
        raw, count = _decode_array(record.get("references"), width=4, maximum=MAX_REFS)
        if not count:
            raise ValueError("RETENTION_RECIPE_MEMBER_INVALID")
        indices = [index for index, in struct.iter_unpack("!I", raw)]
        locations, multiplicities, sliced_total = {}, {}, 0
        for index in indices:
            if index >= len(components):
                raise ValueError("RETENTION_RECIPE_MEMBER_REFERENCE_INVALID")
            used.add(index)
            cid, location = components[index]
            multiplicities[cid] = multiplicities.get(cid, 0) + 1
            if len(location) == 4:
                sliced_total += location[3]
            locations.setdefault(location[0], {})[index] = (cid, location)
        budget = MAX_PACK_BYTES if record.get("recovery") in DEPENDENT_RECOVERIES or record.get("recovery") == "STORED_GZIP_RAW" else size
        if sliced_total > budget:
            raise ValueError("RETENTION_RECIPE_MEMBER_EXPANSION_INVALID")
        # Authenticate one whole pack at a time, even when the wire alternates
        # between packs. Persist only requested bytes, not a many-pack cache.
        captured = {}
        parts = self._components((entry for group in locations.values() for entry in group.values()),
                                 byte_budget=budget, multiplicities=multiplicities)
        for group in locations.values():
            for index, (cid, _) in group.items():
                captured[index] = parts[cid]
        output, total = [], 0
        for index in indices:
            part = captured[index]
            if decode and record.get("recovery") == "STORED_GZIP_RAW":
                part = inflate(part, maximum=size-total)
            total += len(part)
            if total > (MAX_PACK_BYTES if record.get("recovery") in DEPENDENT_RECOVERIES else size):
                raise ValueError("RETENTION_RECIPE_MEMBER_EXPANSION_INVALID")
            output.append(part)
        source = b"".join(output)
        if record.get("recovery") not in DEPENDENT_RECOVERIES and (len(source) != size or sha(source) != record["sha256"]):
            raise ValueError("RETENTION_ARCHIVE_MEMBER_HASH_MISMATCH")
        return source

    def _projection(self, receipt, recipe, components, used, seen=()):
        return self._original(receipt, recipe, components, used, "projection.sqlite", seen)

    def _original(self, receipt, recipe, components, used, name, seen=()):
        # First verify the backwards chain, retaining only small receipts and
        # typed hash/depth metadata. Holding recursive frames retained as many
        # as 33 original 64-MiB images plus all encoded packs at once.
        target = recipe["generation_id"]
        chain, visited = [], set(seen)
        current, current_recipe, current_components = receipt, recipe, components
        while True:
            ident = current_recipe["generation_id"]
            if ident in visited or len(visited) > 32:
                raise ValueError("RETENTION_PAGE_DEPENDENCY_CYCLE_OR_DEPTH")
            visited.add(ident)
            record = current_recipe["members"][name]
            accepted = ({"EXACT_PAGE_PACK", "EXACT_BINARY_PACK"} if name == "projection.sqlite"
                        else {"SOURCE_GZIP_FRAME_CONCAT", "EXACT_BINARY_PACK"})
            if not isinstance(record, dict) or record.get("recovery") not in accepted:
                raise ValueError("RETENTION_RECIPE_RECOVERY_CODEC_INVALID")
            pack = self._member(record, current_components, used if ident == target else set(), decode=False)
            recovery = record.get("recovery")
            if recovery in DEPENDENT_RECOVERIES:
                inspector = inspect_page_pack if recovery == "EXACT_PAGE_PACK" else inspect_binary_pack
                metadata = inspector(pack, expected_pack_sha256=record.get("pack_sha256"),
                    expected_target_sha256=record["sha256"])
            else:
                metadata = {"target_sha256": record["sha256"], "dependency_depth": 0}
            if metadata["dependency_depth"] != integer(record.get("dependency_depth", 0), 32):
                raise ValueError("RETENTION_PAGE_DEPENDENCY_DEPTH_MISMATCH")
            chain.append((current, metadata))
            # Neither original images nor wire bytes survive a traversal step.
            del pack
            self.packs.clear()
            self.recipes.pop(ident, None)
            if not metadata["dependency_depth"]:
                if any(record.get(key) is not None for key in ("base_generation_id", "base_receipt_digest")):
                    raise ValueError("RETENTION_PAGE_INDEPENDENT_BASE_UNEXPECTED")
                break
            base = record.get("base_generation_id")
            if not isinstance(base, str) or ID.fullmatch(base) is None:
                raise ValueError("RETENTION_PAGE_DEPENDENCY_INVALID")
            prior, _ = self.owner._read_control(self.root / (base + ".receipt.json"))
            from rc6_dynamic_universe.common import digest
            if (digest(prior) != record.get("base_receipt_digest")
                    or type(prior.get("receipt_sequence")) is not int
                    or prior["receipt_sequence"] >= current["receipt_sequence"]):
                raise ValueError("RETENTION_PAGE_DEPENDENCY_LINEAGE_INVALID")
            # The declared child depth must agree with every actual predecessor.
            next_recipe = self._recipe(prior)
            base_record = next_recipe["members"].get(name)
            if (not isinstance(base_record, dict)
                    or base_record.get("sha256") != metadata["previous_sha256"]
                    or type(base_record.get("dependency_depth", 0)) is not int
                    or base_record.get("dependency_depth", 0) + 1 != metadata["dependency_depth"]):
                raise ValueError("RETENTION_PAGE_DEPENDENCY_DEPTH_MISMATCH")
            current, current_recipe = prior, next_recipe
            current_components = self._indices(current_recipe)

        # Reopen and verify the exact original pack at each forward step, then
        # consume only the preceding reconstructed image. No encoder or image
        # cache is used, even between two restores on the same public verifier.
        previous, depth = None, None
        for current, expected in reversed(chain):
            current_recipe = self._recipe(current)
            current_components = self._indices(current_recipe)
            ident = current_recipe["generation_id"]
            record = current_recipe["members"][name]
            pack = self._member(record, current_components, used if ident == target else set(), decode=False)
            recovery = record.get("recovery")
            if recovery in DEPENDENT_RECOVERIES:
                inspector = inspect_page_pack if recovery == "EXACT_PAGE_PACK" else inspect_binary_pack
                metadata = inspector(pack, expected_pack_sha256=record.get("pack_sha256"),
                    expected_target_sha256=record["sha256"])
            else:
                metadata = {"target_sha256": record["sha256"], "dependency_depth": 0}
            if metadata != expected:
                raise ValueError("RETENTION_PAGE_DEPENDENCY_CHANGED")
            if recovery in DEPENDENT_RECOVERIES:
                decoder = decode_page_pack if recovery == "EXACT_PAGE_PACK" else decode_binary_pack
                source = decoder(pack, expected_pack_sha256=record["pack_sha256"],
                    expected_target_sha256=record["sha256"], previous_bytes=previous, previous_depth=depth)
            else:
                source = self._member(record, current_components, used if ident == target else set())
            if len(source) != record["bytes"]:
                raise ValueError("RETENTION_PAGE_SOURCE_LENGTH_MISMATCH")
            previous, depth = source, metadata["dependency_depth"]
            del pack, source
            self.packs.clear()
            self.recipes.pop(ident, None)
        return previous, depth

    def restore(self, receipt):
        # Proof/bytes caches belong to one verification invocation. A repeat
        # read must observe corruption or replaced members, not prior objects.
        self.packs.clear(); self.recipes.clear(); self.projections.clear()
        recipe = self._recipe(receipt)
        members = recipe.get("members")
        if not isinstance(members, dict) or set(members) not in (BASE_MEMBERS, MEMBERS):
            raise ValueError("RETENTION_RECIPE_MEMBER_SET_INVALID")
        components, used, restored = self._indices(recipe), set(), {}
        for name, record in members.items():
            expected = "EXACT_PAGE_PACK" if name == "projection.sqlite" else "SOURCE_GZIP_FRAME_CONCAT" if name.endswith(".gz") else "STORED_GZIP_RAW"
            accepted = {expected, "EXACT_BINARY_PACK"} if name in {"projection.sqlite", "checkpoint.json.gz"} else {expected}
            if not isinstance(record, dict) or record.get("recovery") not in accepted:
                raise ValueError("RETENTION_RECIPE_RECOVERY_CODEC_INVALID")
            if name == "projection.sqlite":
                restored[name], _ = self._projection(receipt, recipe, components, used)
            elif record["recovery"] == "EXACT_BINARY_PACK":
                restored[name], _ = self._original(receipt, recipe, components, used, name)
            else:
                restored[name] = self._member(record, components, used)
        if used != set(range(len(components))):
            raise ValueError("RETENTION_RECIPE_UNUSED_COMPONENT")
        manifest = validate_materialized(restored, expected_manifest_sha256=receipt["manifest_sha256"])
        links = {key: manifest.get(key) for key in ("generation_id", "sequence", "previous_generation_id", "previous_manifest_sha256",
            "as_of", "source_watermark", "source_audit_digest", "source_reports_digest", "role_headers")}
        if recipe.get("manifest_links") != links:
            raise ValueError("RETENTION_RECIPE_MANIFEST_LINK_MISMATCH")
        return restored, manifest

    def build(self, source, manifest, manifest_sha):
        ident = manifest["generation_id"]
        directory = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY | getattr(os, "O_NOATIME", 0))
        try:
            source_info = _metadata(os.fstat(directory), directory=True)
            names = sorted(os.listdir(directory))
            if set(names) not in (BASE_MEMBERS, MEMBERS):
                raise ValueError("RETENTION_ARCHIVE_OBJECT_SHAPE_INVALID")
        finally:
            os.close(directory)
        captured = {name: read(source / name, maximum=MAX_MEMBER_BYTES) for name in names}
        original = {name: value[0] for name, value in captured.items()}
        def unchanged():
            for name, evidence in captured.items():
                if read(source / name, maximum=MAX_MEMBER_BYTES) != evidence:
                    raise ValueError("RETENTION_COMPONENT_SOURCE_CHANGED")
            if _metadata(source.lstat(), directory=True) != source_info:
                raise ValueError("RETENTION_COMPONENT_SOURCE_CHANGED")
        validate_materialized(original, expected_manifest_sha256=manifest_sha)
        head = self.owner._archive_checkpoint()
        recipe_path = self.root / (ident + ".recipe.gz")
        if recipe_path.exists() or recipe_path.is_symlink():
            recipe_wire, _ = read(recipe_path, maximum=MAX_RECIPE_BYTES)
            preview = {"schema": ACK_SCHEMA, "archiver_id": ARCHIVER_ID, "generation_id": ident,
                "sequence": manifest["sequence"], "manifest_sha256": manifest_sha,
                "archive_sha256": sha(recipe_wire), "archive_uri": "local-private://"+ident+".recipe.gz",
                "receipt_sequence": head["receipt_count"]+1}
            restored, _ = self.restore(preview)
            if restored != original:
                raise ValueError("RETENTION_COMPONENT_SOURCE_CHANGED")
            unchanged()
            return preview
        base_receipt, prior_recipe, previous, previous_depth, checkpoint, checkpoint_depth, common_depth = None, None, None, 0, None, 0, 0
        if head.get("generation_id"):
            prior, _ = self.owner._read_control(self.root / (head["generation_id"] + ".receipt.json"))
            if prior.get("schema") == ACK_SCHEMA:
                prior_recipe = self._recipe(prior)
                base_receipt = prior
                common_depth = self._common_dependency_depth(prior)
                if "projection.sqlite" in prior_recipe["members"]:
                    previous, previous_depth = self._projection(prior, prior_recipe, self._indices(prior_recipe), set())
                if "checkpoint.json.gz" in prior_recipe["members"]:
                    checkpoint, checkpoint_depth = self._original(prior, prior_recipe, self._indices(prior_recipe), set(), "checkpoint.json.gz")
        catalog = self._catalog()
        slice_catalog = None
        if prior_recipe is not None:
            try:
                slice_catalog = self._slice_catalog(catalog, prior_recipe)
            except ValueError as error:
                if str(error) != "RETENTION_COMPONENT_INDEX_CAPACITY_REACHED":
                    raise
        alternatives = {}
        force_full = bool(manifest["sequence"] % 32 == 0) or common_depth == 32
        def dependent_record(raw, recovery, packed, info):
            from rc6_dynamic_universe.common import digest
            return {"sha256": sha(raw), "bytes": len(raw), "recovery": recovery,
                "pack_sha256": sha(packed), "dependency_depth": info["dependency_depth"],
                "base_generation_id": base_receipt["generation_id"] if info["dependency_depth"] else None,
                "base_receipt_digest": digest(base_receipt) if info["dependency_depth"] else None}
        for name, raw in original.items():
            record = {"sha256": sha(raw), "bytes": len(raw)}
            if name == "projection.sqlite":
                page, info = encode_page_pack(raw, previous, previous_depth=previous_depth,
                    force_full=force_full)
                parts, record = (page,), dependent_record(raw, "EXACT_PAGE_PACK", page, info)
            elif name.endswith(".gz"):
                parts, record["recovery"] = source_gzip_frames(raw), "SOURCE_GZIP_FRAME_CONCAT"
            else:
                parts, record["recovery"] = (gzip.compress(raw, mtime=0, compresslevel=1),), "STORED_GZIP_RAW"
            alternatives[name] = [(parts, record)]
            if slice_catalog is not None and record["recovery"] != "STORED_GZIP_RAW":
                sliced = tuple(part for piece in parts for part in exact_wire_slices(piece))
                if sliced != parts:
                    alternatives[name].append((sliced, record))
            if name == "projection.sqlite":
                grouped = grouped_wire_parts(page, "EXACT_PAGE_PACK",
                    expected_pack_sha256=record["pack_sha256"], expected_target_sha256=record["sha256"])
                alternatives[name].append((grouped, record))
            if name in {"projection.sqlite", "checkpoint.json.gz"}:
                base, depth = (previous, previous_depth) if name == "projection.sqlite" else (checkpoint, checkpoint_depth)
                proposal = self._binary_option(raw, base, depth, force_full,
                                               allow_xor=name == "projection.sqlite")
                if proposal is not None:
                    binary, info = proposal
                    alternatives[name].append(((binary,), dependent_record(raw, "EXACT_BINARY_PACK", binary, info)))
        # Authenticate the catalog once; all alternatives reuse this same
        # captured base. Sequential plans retain one winner, never four packs.
        # The unchanged report frame layout is not a second codec experiment.
        recipe_wire, packed, new_name = self._select_plan(alternatives, catalog, manifest, manifest_sha,
                                                       slice_catalog=slice_catalog)
        # Exact new bytes plus simultaneous controls, rounded physical blocks.
        additions = (len(packed) if packed is not None else 0) + len(recipe_wire)
        self.owner._archive_inventory(additional_bytes=additions+4*65536, additional_files=6)
        self.owner._write_archive_build_intent(ident, manifest_sha,
            recipe_sha256=sha(recipe_wire), pack_name=new_name)
        if packed is not None:
            self._publish(self.root / new_name, packed, prefix=".cas-")
            self.owner._fault("archive_after_publish_components")
        self._publish(self.root / (ident + ".recipe.gz"), recipe_wire, prefix=".recipe-")
        self.owner._fault("archive_after_publish_recipe")
        preview = {"schema": ACK_SCHEMA, "archiver_id": ARCHIVER_ID, "generation_id": ident,
            "sequence": manifest["sequence"], "manifest_sha256": manifest_sha,
            "archive_sha256": sha(recipe_wire), "archive_uri": "local-private://"+ident+".recipe.gz",
            "receipt_sequence": head["receipt_count"]+1}
        restored, _ = self.restore(preview)
        if restored != original:
            raise ValueError("RETENTION_COMPONENT_SOURCE_CHANGED")
        unchanged()
        return preview

    def _select_plan(self, alternatives, catalog, manifest, manifest_sha, *, slice_catalog=None):
        """Keep the original first plan unless both complete new costs improve.

        Costs include whole newly written CAS packs (headers and records) and
        the actual compressed recipe/index/reference wire. Existing shared
        packs remain physical whole objects; this grants no GC credit or
        directory/temporary/recovery bound. The archive owner's live inventory
        and final Horizon physical proof remain separately mandatory.
        """
        verified_reuse, selected, baseline_cost = {}, None, None
        block = os.statvfs(self.root).f_frsize
        if type(block) is not int or block <= 0:
            raise ValueError("RETENTION_ARCHIVE_BLOCK_SIZE_INVALID")
        for choices in product(*(range(len(options)) for options in alternatives.values())):
            options = {name: alternatives[name][choice] for name, choice in zip(alternatives, choices)}
            try:
                active_catalog = slice_catalog if slice_catalog is not None and selected is not None else catalog
                plan = self._plan(options, active_catalog, manifest, manifest_sha, verified_reuse)
            except ValueError as error:
                # An optional layout cannot turn an admissible original into
                # a catalog/pack overflow. Custody, hash and typed-index errors
                # remain fatal even when encountered in an optional proposal.
                if selected is None or str(error) not in {
                        "RETENTION_COMPONENT_INDEX_CAPACITY_REACHED",
                        "RETENTION_COMPONENT_PACK_CAPACITY_REACHED",
                        "RETENTION_RECIPE_CAPACITY_REACHED"}:
                    raise
                continue
            recipe_wire, packed, new_name = plan
            sizes = (len(recipe_wire), len(packed) if packed is not None else 0)
            cost = (sum((size + block - 1) // block * block for size in sizes), sum(sizes))
            if baseline_cost is None:
                baseline_cost, selected, selected_cost = cost, plan, cost
            elif (cost[0] <= baseline_cost[0] and cost[1] <= baseline_cost[1] and cost < selected_cost):
                selected, selected_cost = plan, cost
            del plan, packed, recipe_wire
        return selected

    def _plan(self, options, catalog, manifest, manifest_sha, verified_reuse):
        components, component_ids, members, fresh = [], {}, {}, {}
        candidates = {}
        for parts, _ in options.values():
            for part in parts:
                cid = sha(part)
                if cid in catalog and cid not in verified_reuse:
                    if cid in candidates and candidates[cid] != part:
                        raise ValueError("RETENTION_COMPONENT_HASH_COLLISION")
                    candidates[cid] = part
        originals = self._components(((cid, catalog[cid]) for cid in candidates),
                                    byte_budget=sum(map(len, candidates.values())))
        for cid, part in candidates.items():
            if originals.pop(cid) != part:
                raise ValueError("RETENTION_COMPONENT_HASH_COLLISION")
            verified_reuse[cid] = part
        def add(part):
            cid = sha(part)
            if cid not in component_ids:
                if len(components) >= MAX_COMPONENTS:
                    raise ValueError("RETENTION_COMPONENT_INDEX_CAPACITY_REACHED")
                component_ids[cid] = len(components); components.append(cid)
                if cid in catalog:
                    if verified_reuse[cid] != part:
                        raise ValueError("RETENTION_COMPONENT_HASH_COLLISION")
                else:
                    fresh[cid] = part
            elif cid in fresh and fresh[cid] != part:
                raise ValueError("RETENTION_COMPONENT_HASH_COLLISION")
            return component_ids[cid]
        for name, (parts, descriptor) in options.items():
            if len(parts) > MAX_REFS:
                raise ValueError("RETENTION_COMPONENT_INDEX_CAPACITY_REACHED")
            record = dict(descriptor)
            refs = [add(part) for part in parts]
            record["references"] = _array(b"".join(struct.pack("!I", index) for index in refs), count=len(refs))
            members[name] = record
        if getattr(catalog, "physical_records", len(catalog)) + len(fresh) > MAX_COMPONENTS:
            raise ValueError("RETENTION_COMPONENT_INDEX_CAPACITY_REACHED")
        packed, new_name = None, None
        if fresh:
            cursor = PACK_HEADER.size + len(fresh) * PACK_RECORD.size
            index, payloads = [], []
            for ordinal, (cid, raw) in enumerate(fresh.items()):
                index.append(PACK_RECORD.pack(bytes.fromhex(cid), cursor, len(raw))); payloads.append(raw); cursor += len(raw)
            if cursor > MAX_PACK_BYTES:
                raise ValueError("RETENTION_COMPONENT_PACK_CAPACITY_REACHED")
            packed = PACK_HEADER.pack(PACK_MAGIC, len(fresh)) + b"".join(index) + b"".join(payloads)
            new_name = sha(packed) + ".cas.pack"
        locations = {cid: (new_name, ordinal) for ordinal, cid in enumerate(fresh)}
        packs, pack_ids, rows = [], {}, []
        sliced = any(cid not in locations and len(catalog[cid]) == 4 for cid in components)
        for cid in components:
            location = locations[cid] if cid in locations else catalog[cid]
            name, ordinal = location[:2]
            if name not in pack_ids:
                pack_ids[name] = len(packs); packs.append(name)
            if sliced:
                start, length = location[2:] if len(location) == 4 else (0, len(fresh[cid]) if cid in fresh else len(verified_reuse[cid]))
                rows.append(SLICE_COMPONENT_RECORD.pack(pack_ids[name], ordinal, start, length, bytes.fromhex(cid)))
            else:
                rows.append(COMPONENT_RECORD.pack(pack_ids[name], ordinal, bytes.fromhex(cid)))
        recipe = {"schema": SLICE_RECIPE_SCHEMA if sliced else RECIPE_SCHEMA, "generation_id": manifest["generation_id"], "sequence": manifest["sequence"],
            "manifest_sha256": manifest_sha, "members": members, "packs": packs,
            "components": _array(b"".join(rows), count=len(rows), codec=SLICE_ARRAY_CODEC if sliced else "GZIP_BIG_ENDIAN_BINARY_INDEX_V1"),
            "manifest_links": {key: manifest.get(key) for key in ("generation_id", "sequence", "previous_generation_id",
                "previous_manifest_sha256", "as_of", "source_watermark", "source_audit_digest", "source_reports_digest", "role_headers")},
            "verification_level": LEVEL, "custody": "LOCAL_PRIVATE_FSYNC_NOT_WORM"}
        recipe_raw = canonical(recipe)
        if len(recipe_raw) > MAX_RECIPE_BYTES:
            raise ValueError("RETENTION_RECIPE_CAPACITY_REACHED")
        recipe_wire = gzip.compress(recipe_raw, mtime=0, compresslevel=1)
        return recipe_wire, packed, new_name

    def dependency_graph(self, receipts):
        """Verify recipe/CID metadata and page-chain depth before any GC plan.

        The receipt chain is verified by the owner. Every referenced pack and
        component is byte-verified here; only page wire/index metadata is used
        for reachability. This does not advertise full source reconstruction.
        """
        graph, active = {}, set()
        def visit(receipt):
            ident = receipt["generation_id"]
            if ident in active:
                raise ValueError("RETENTION_PAGE_DEPENDENCY_CYCLE_OR_DEPTH")
            if ident in graph:
                return graph[ident]
            if len(active) > 32:
                raise ValueError("RETENTION_PAGE_DEPENDENCY_CYCLE_OR_DEPTH")
            active.add(ident)
            recipe = self._recipe(receipt)
            components = self._indices(recipe)
            self._components(components, byte_budget=MAX_PACK_BYTES * len(MEMBERS),
                             visitor=lambda cid, location, view: None)
            base, depth = None, 0
            for member_name in ("projection.sqlite", "checkpoint.json.gz"):
                if member_name not in recipe["members"]:
                    continue
                record = recipe["members"][member_name]
                accepted = ({"EXACT_PAGE_PACK", "EXACT_BINARY_PACK"} if member_name == "projection.sqlite"
                            else {"SOURCE_GZIP_FRAME_CONCAT", "EXACT_BINARY_PACK"})
                if not isinstance(record, dict) or record.get("recovery") not in accepted:
                    raise ValueError("RETENTION_RECIPE_MEMBER_INVALID")
                if record["recovery"] not in DEPENDENT_RECOVERIES:
                    continue
                page = self._member(record, components, set(), decode=False)
                inspector = inspect_page_pack if record["recovery"] == "EXACT_PAGE_PACK" else inspect_binary_pack
                metadata = inspector(page, expected_pack_sha256=record.get("pack_sha256"),
                    expected_target_sha256=record["sha256"])
                member_depth = integer(record.get("dependency_depth"), 32)
                if member_depth != metadata["dependency_depth"]:
                    raise ValueError("RETENTION_PAGE_DEPENDENCY_DEPTH_MISMATCH")
                depth = max(depth, member_depth)
                if member_depth:
                    member_base = record.get("base_generation_id")
                    if (not isinstance(member_base, str) or ID.fullmatch(member_base) is None
                            or base is not None and base != member_base):
                        raise ValueError("RETENTION_PAGE_DEPENDENCY_INVALID")
                    base = member_base
                    previous, _ = self.owner._read_control(self.root / (base + ".receipt.json"))
                    from rc6_dynamic_universe.common import digest
                    if (digest(previous) != record.get("base_receipt_digest")
                            or type(previous.get("receipt_sequence")) is not int
                            or previous["receipt_sequence"] >= receipt["receipt_sequence"]):
                        raise ValueError("RETENTION_PAGE_DEPENDENCY_LINEAGE_INVALID")
                    prior = visit(previous)
                    prior_recipe = self._recipe(previous)
                    prior_member = prior_recipe["members"].get(member_name)
                    if (not isinstance(prior_member, dict)
                            or integer(prior_member.get("dependency_depth", 0), 32) + 1 != member_depth
                            or prior_member["sha256"] != metadata["previous_sha256"]):
                        raise ValueError("RETENTION_PAGE_DEPENDENCY_DEPTH_MISMATCH")
                    depth = max(depth, prior["depth"] + 1)
                    if depth > 32:
                        raise ValueError("RETENTION_PAGE_DEPENDENCY_CYCLE_OR_DEPTH")
                elif any(record.get(key) is not None for key in ("base_generation_id", "base_receipt_digest")):
                    raise ValueError("RETENTION_PAGE_INDEPENDENT_BASE_UNEXPECTED")
            node = {"base_generation_id": base, "depth": depth, "packs": tuple(recipe["packs"]),
                    "as_of": recipe["manifest_links"].get("as_of")}
            graph[ident] = node
            active.remove(ident)
            # GC needs immutable path/hash metadata, not every historical pack
            # in RAM. Full reconstruction has a separate call-scoped cache.
            self.packs.clear()
            return node
        for receipt in receipts:
            visit(receipt)
        return graph


def validate_materialized(members, *, expected_manifest_sha256):
    """Verify every original byte/hash/CRC and typed sealed role links."""
    if not isinstance(members, dict) or set(members) not in (BASE_MEMBERS, MEMBERS):
        raise ValueError("RETENTION_ARCHIVE_OBJECT_SHAPE_INVALID")
    if sha(members["manifest.json"]) != expected_manifest_sha256:
        raise ValueError("RETENTION_ARCHIVE_MANIFEST_MISMATCH")
    manifest = loads(members["manifest.json"])
    names = {"report": "report.json.gz", "checkpoint": "checkpoint.json.gz", "status": "status.json"}
    if "projection.sqlite" in members: names["projection"] = "projection.sqlite"
    if (not isinstance(manifest, dict) or manifest.get("schema") not in {"rc6.shadow-evidence-generation.v1", "rc6.shadow-evidence-generation.v2"}
            or not isinstance(manifest.get("files"), dict) or set(manifest["files"]) != set(names)
            or not isinstance(manifest.get("generation_id"), str) or ID.fullmatch(manifest["generation_id"]) is None
            or type(manifest.get("sequence")) is not int or manifest["sequence"] <= 0):
        raise ValueError("RETENTION_ARCHIVE_MANIFEST_MISMATCH")
    proofs, payloads = {}, {}
    from .serialization import is_storage, verify_storage_wire, canonical_metrics, decode_storage, STORAGE_SCHEMAS
    for role, name in names.items():
        record, raw = manifest["files"][role], members[name]
        if (not isinstance(record, dict) or record.get("name") != name or sha(raw) != record.get("sha256")
                or len(raw) > MAX_MEMBER_BYTES):
            raise ValueError("RETENTION_ARCHIVE_MEMBER_HASH_MISMATCH")
        if role == "projection":
            from .projection import open_projection, logical_digest
            connection, header = open_projection(raw)
            try:
                if (header.get("generation_id") != manifest["generation_id"]
                        or header.get("derivation") != {key: proofs[key] for key in ("report", "checkpoint", "status")}
                        or logical_digest(connection, header)[0] != record["payload_digest"]):
                    raise ValueError("RETENTION_ARCHIVE_PROJECTION_LINK_MISMATCH")
            finally:
                connection.close()
            continue
        if name.endswith(".gz"):
            frames = source_gzip_frames(raw)
            wire = b"".join(inflate(frame, maximum=MAX_MEMBER_BYTES) for frame in frames)
        else:
            wire = raw
        envelope = loads(wire)
        if not isinstance(envelope, dict) or set(envelope) != {"digest", "payload"}:
            raise ValueError("RETENTION_ARCHIVE_EVIDENCE_INVALID")
        stored = envelope["payload"]
        if (isinstance(stored, dict) and isinstance(stored.get("schema"), str)
                and stored["schema"].startswith("rc6.lossless-json-storage.") and stored["schema"] not in STORAGE_SCHEMAS):
            raise ValueError("RETENTION_ARCHIVE_STORAGE_SCHEMA_UNSUPPORTED")
        proof = (verify_storage_wire(stored, durable_limit=MAX_MEMBER_BYTES, expansion_limit=512*1024**2)
                 if is_storage(stored) else {"payload_digest": canonical_metrics(stored, ensure_ascii=True, limit=512*1024**2)[0]})
        if record.get("payload_digest") != proof["payload_digest"] or envelope["digest"] != proof["payload_digest"]:
            raise ValueError("RETENTION_ARCHIVE_PAYLOAD_DIGEST_MISMATCH")
        proofs[role] = proof["payload_digest"]
        if role == "status" and is_storage(stored):
            payloads[role] = decode_storage(stored, durable_limit=MAX_MEMBER_BYTES, expansion_limit=512*1024**2)
        elif not is_storage(stored):
            payloads[role] = stored
    if manifest["schema"] == "rc6.shadow-evidence-generation.v2":
        from .persistence import _semantic_safety
        headers = manifest.get("role_headers")
        if (not isinstance(headers, dict) or set(headers) != set(names)
                or any(not isinstance(header, dict) for header in headers.values())):
            raise ValueError("RETENTION_ARCHIVE_ROLE_HEADER_INVALID")
        _semantic_safety(headers)
        for role, header in headers.items():
            if any(header.get(key) != manifest.get(key) for key in ("generation_id", "sequence", "as_of", "source_watermark", "configuration_fingerprint")):
                raise ValueError("RETENTION_ARCHIVE_ROLE_HEADER_INVALID")
        for role, payload in payloads.items():
            if not isinstance(payload, dict):
                raise ValueError("RETENTION_ARCHIVE_EVIDENCE_INVALID")
            _semantic_safety({role: payload})
            if any(payload.get(key) != value for key, value in headers[role].items()):
                raise ValueError("RETENTION_ARCHIVE_ROLE_HEADER_INVALID")
        status = payloads["status"]
        if status.get("report_digest") != proofs["report"] or status.get("checkpoint_digest") != proofs["checkpoint"]:
            raise ValueError("RETENTION_ARCHIVE_CROSS_PAYLOAD_LINK_MISMATCH")
    return manifest
