"""Exact original component archive; recipes confer no trading authority.

Immutable pack headers are lookup hints. Before reuse or restoration the whole
pack, its original component CID, and every original source-member SHA are
verified. There is no mutable CAS database and restore never calls an encoder.
The owner supplies its archive/writer locks, quota admission and receipt chain.
"""
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import uuid
import zlib

from .exact_page_storage import encode_page_pack, decode_page_pack, inspect_page_pack


RECIPE_SCHEMA = "RC6_SHADOW_ARCHIVE_COMPONENT_RECIPE_V3"
ACK_SCHEMA = "RC6_SHADOW_ARCHIVE_ACK_V3"
ARCHIVER_ID = "RC6_LOCAL_PRIVATE_COMPONENT_ARCHIVER_V3"
PACK_MAGIC = b"RC6CASP3"
PACK_HEADER = struct.Struct("!8sI")
PACK_RECORD = struct.Struct("!32sII")
COMPONENT_RECORD = struct.Struct("!II32s")
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


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


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


def _array(raw, *, count):
    encoded = gzip.compress(raw, mtime=0, compresslevel=1)
    return {"codec": "GZIP_BIG_ENDIAN_BINARY_INDEX_V1", "count": count,
        "sha256": sha(encoded), "payload": base64.b64encode(encoded).decode("ascii")}


def _decode_array(record, *, width, maximum):
    if (not isinstance(record, dict) or set(record) != {"codec", "count", "sha256", "payload"}
            or record["codec"] != "GZIP_BIG_ENDIAN_BINARY_INDEX_V1"
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

    def _pack(self, name):
        match = PACK_NAME.fullmatch(name) if isinstance(name, str) else None
        if match is None:
            raise ValueError("RETENTION_COMPONENT_PACK_PATH_INVALID")
        if name not in self.packs:
            raw, _ = read(self.root / name, maximum=MAX_PACK_BYTES)
            if sha(raw) != match[1]:
                raise ValueError("RETENTION_COMPONENT_PACK_HASH_MISMATCH")
            self.packs[name] = raw, _pack_index(raw, len(raw))
        return self.packs[name]

    def _catalog(self):
        """Headers locate candidate bytes; they never authenticate a component."""
        catalog, count = {}, 0
        for path in sorted(self.root.glob("*.cas.pack")):
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
        return catalog

    def _component(self, cid, location):
        if not isinstance(cid, str) or HEX.fullmatch(cid) is None:
            raise ValueError("RETENTION_COMPONENT_CID_INVALID")
        name, ordinal = location
        raw, rows = self._pack(name)
        integer(ordinal, MAX_COMPONENTS)
        if ordinal >= len(rows) or rows[ordinal][0] != cid:
            raise ValueError("RETENTION_COMPONENT_BINDING_MISMATCH")
        _, offset, size = rows[ordinal]
        original = raw[offset:offset+size]
        if sha(original) != cid:
            raise ValueError("RETENTION_COMPONENT_HASH_MISMATCH")
        return original

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
                    or value.get("schema") != RECIPE_SCHEMA or value.get("generation_id") != ident
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
        raw, count = _decode_array(recipe.get("components"), width=COMPONENT_RECORD.size, maximum=MAX_COMPONENTS)
        if not count:
            raise ValueError("RETENTION_RECIPE_COMPONENT_INDEX_INVALID")
        rows, used_packs, seen = [], set(), set()
        for pack, ordinal, cid in COMPONENT_RECORD.iter_unpack(raw):
            if pack >= len(packs) or ordinal >= MAX_COMPONENTS or cid in seen:
                raise ValueError("RETENTION_RECIPE_COMPONENT_INDEX_INVALID")
            seen.add(cid); used_packs.add(pack); rows.append((cid.hex(), (packs[pack], ordinal)))
        if used_packs != set(range(len(packs))):
            raise ValueError("RETENTION_RECIPE_UNUSED_PACK")
        return rows

    def _member(self, record, components, used, *, decode=True):
        expected = {"sha256", "bytes", "recovery", "references"}
        if isinstance(record, dict) and record.get("recovery") == "EXACT_PAGE_PACK":
            expected |= {"pack_sha256", "dependency_depth", "base_generation_id", "base_receipt_digest"}
        if (not isinstance(record, dict) or set(record) != expected
                or not isinstance(record.get("sha256"), str) or HEX.fullmatch(record["sha256"]) is None):
            raise ValueError("RETENTION_RECIPE_MEMBER_INVALID")
        size = integer(record.get("bytes"), MAX_MEMBER_BYTES, positive=True)
        raw, count = _decode_array(record.get("references"), width=4, maximum=MAX_REFS)
        if not count:
            raise ValueError("RETENTION_RECIPE_MEMBER_INVALID")
        output, total = [], 0
        for index, in struct.iter_unpack("!I", raw):
            if index >= len(components):
                raise ValueError("RETENTION_RECIPE_MEMBER_REFERENCE_INVALID")
            used.add(index)
            cid, location = components[index]
            part = self._component(cid, location)
            if decode and record.get("recovery") == "STORED_GZIP_RAW":
                part = inflate(part, maximum=size-total)
            total += len(part)
            if total > (MAX_PACK_BYTES if record.get("recovery") == "EXACT_PAGE_PACK" else size):
                raise ValueError("RETENTION_RECIPE_MEMBER_EXPANSION_INVALID")
            output.append(part)
        source = b"".join(output)
        if record.get("recovery") != "EXACT_PAGE_PACK" and (len(source) != size or sha(source) != record["sha256"]):
            raise ValueError("RETENTION_ARCHIVE_MEMBER_HASH_MISMATCH")
        return source

    def _projection(self, receipt, recipe, components, used, seen=()):
        ident = recipe["generation_id"]
        if ident in seen or len(seen) > 32:
            raise ValueError("RETENTION_PAGE_DEPENDENCY_CYCLE_OR_DEPTH")
        if ident in self.projections:
            return self.projections[ident]
        record = recipe["members"]["projection.sqlite"]
        pack = self._member(record, components, used, decode=False)
        pack_hash = record.get("pack_sha256")
        metadata = inspect_page_pack(pack, expected_pack_sha256=pack_hash, expected_target_sha256=record["sha256"])
        if metadata["dependency_depth"] != integer(record.get("dependency_depth"), 32):
            raise ValueError("RETENTION_PAGE_DEPENDENCY_DEPTH_MISMATCH")
        previous, depth = None, None
        if metadata["dependency_depth"]:
            base = record.get("base_generation_id")
            if not isinstance(base, str) or ID.fullmatch(base) is None:
                raise ValueError("RETENTION_PAGE_DEPENDENCY_INVALID")
            prior, _ = self.owner._read_control(self.root / (base + ".receipt.json"))
            from rc6_dynamic_universe.common import digest
            if (digest(prior) != record.get("base_receipt_digest")
                    or type(prior.get("receipt_sequence")) is not int
                    or prior["receipt_sequence"] >= receipt["receipt_sequence"]):
                raise ValueError("RETENTION_PAGE_DEPENDENCY_LINEAGE_INVALID")
            prior_recipe = self._recipe(prior)
            prior_components = self._indices(prior_recipe)
            previous, depth = self._projection(prior, prior_recipe, prior_components, set(), (*seen, ident))
            if sha(previous) != metadata["previous_sha256"]:
                raise ValueError("RETENTION_PAGE_DEPENDENCY_HASH_MISMATCH")
        elif any(record.get(key) is not None for key in ("base_generation_id", "base_receipt_digest")):
            raise ValueError("RETENTION_PAGE_INDEPENDENT_BASE_UNEXPECTED")
        source = decode_page_pack(pack, expected_pack_sha256=pack_hash, expected_target_sha256=record["sha256"],
            previous_bytes=previous, previous_depth=depth)
        if len(source) != record["bytes"]:
            raise ValueError("RETENTION_PAGE_SOURCE_LENGTH_MISMATCH")
        self.projections[ident] = source, metadata["dependency_depth"]
        return self.projections[ident]

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
            if not isinstance(record, dict) or record.get("recovery") != expected:
                raise ValueError("RETENTION_RECIPE_RECOVERY_CODEC_INVALID")
            if name == "projection.sqlite":
                restored[name], _ = self._projection(receipt, recipe, components, used)
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
            names = os.listdir(directory)
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
        base_receipt, previous, previous_depth = None, None, 0
        if "projection.sqlite" in original and head.get("generation_id"):
            prior, _ = self.owner._read_control(self.root / (head["generation_id"] + ".receipt.json"))
            if prior.get("schema") == ACK_SCHEMA:
                prior_recipe = self._recipe(prior)
                if "projection.sqlite" in prior_recipe["members"]:
                    previous, previous_depth = self._projection(prior, prior_recipe, self._indices(prior_recipe), set())
                    base_receipt = prior
        catalog = self._catalog()
        components, component_ids, members, fresh = [], {}, {}, {}
        def add(part):
            cid = sha(part)
            if cid not in component_ids:
                component_ids[cid] = len(components); components.append(cid)
                if cid in catalog:
                    if self._component(cid, catalog[cid]) != part:
                        raise ValueError("RETENTION_COMPONENT_HASH_COLLISION")
                else:
                    fresh[cid] = part
            return component_ids[cid]
        for name, raw in original.items():
            record = {"sha256": sha(raw), "bytes": len(raw)}
            if name == "projection.sqlite":
                page, info = encode_page_pack(raw, previous, previous_depth=previous_depth,
                    force_full=bool(manifest["sequence"] % 32 == 0))
                parts, record["recovery"] = (page,), "EXACT_PAGE_PACK"
                from rc6_dynamic_universe.common import digest
                record.update(pack_sha256=sha(page), dependency_depth=info["dependency_depth"],
                    base_generation_id=base_receipt["generation_id"] if info["dependency_depth"] else None,
                    base_receipt_digest=digest(base_receipt) if info["dependency_depth"] else None)
            elif name.endswith(".gz"):
                parts, record["recovery"] = source_gzip_frames(raw), "SOURCE_GZIP_FRAME_CONCAT"
            else:
                parts, record["recovery"] = (gzip.compress(raw, mtime=0, compresslevel=1),), "STORED_GZIP_RAW"
            refs = [add(part) for part in parts]
            record["references"] = _array(b"".join(struct.pack("!I", index) for index in refs), count=len(refs))
            members[name] = record
        if len(catalog) + len(fresh) > MAX_COMPONENTS:
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
            for ordinal, cid in enumerate(fresh): catalog[cid] = new_name, ordinal
        packs, pack_ids, rows = [], {}, []
        for cid in components:
            name, ordinal = catalog[cid]
            if name not in pack_ids:
                pack_ids[name] = len(packs); packs.append(name)
            rows.append(COMPONENT_RECORD.pack(pack_ids[name], ordinal, bytes.fromhex(cid)))
        recipe = {"schema": RECIPE_SCHEMA, "generation_id": ident, "sequence": manifest["sequence"],
            "manifest_sha256": manifest_sha, "members": members, "packs": packs,
            "components": _array(b"".join(rows), count=len(rows)),
            "manifest_links": {key: manifest.get(key) for key in ("generation_id", "sequence", "previous_generation_id",
                "previous_manifest_sha256", "as_of", "source_watermark", "source_audit_digest", "source_reports_digest", "role_headers")},
            "verification_level": LEVEL, "custody": "LOCAL_PRIVATE_FSYNC_NOT_WORM"}
        recipe_raw = canonical(recipe)
        if len(recipe_raw) > MAX_RECIPE_BYTES:
            raise ValueError("RETENTION_RECIPE_CAPACITY_REACHED")
        recipe_wire = gzip.compress(recipe_raw, mtime=0, compresslevel=1)
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
            for cid, location in components:
                self._component(cid, location)
            base, depth = None, 0
            if "projection.sqlite" in recipe["members"]:
                record = recipe["members"]["projection.sqlite"]
                if not isinstance(record, dict) or record.get("recovery") != "EXACT_PAGE_PACK":
                    raise ValueError("RETENTION_RECIPE_MEMBER_INVALID")
                page = self._member(record, components, set(), decode=False)
                metadata = inspect_page_pack(page, expected_pack_sha256=record.get("pack_sha256"),
                    expected_target_sha256=record["sha256"])
                depth = integer(record.get("dependency_depth"), 32)
                if depth != metadata["dependency_depth"]:
                    raise ValueError("RETENTION_PAGE_DEPENDENCY_DEPTH_MISMATCH")
                if depth:
                    base = record.get("base_generation_id")
                    if not isinstance(base, str) or ID.fullmatch(base) is None:
                        raise ValueError("RETENTION_PAGE_DEPENDENCY_INVALID")
                    previous, _ = self.owner._read_control(self.root / (base + ".receipt.json"))
                    from rc6_dynamic_universe.common import digest
                    if (digest(previous) != record.get("base_receipt_digest")
                            or type(previous.get("receipt_sequence")) is not int
                            or previous["receipt_sequence"] >= receipt["receipt_sequence"]):
                        raise ValueError("RETENTION_PAGE_DEPENDENCY_LINEAGE_INVALID")
                    prior = visit(previous)
                    prior_recipe = self._recipe(previous)
                    if (prior["depth"]+1 != depth
                            or prior_recipe["members"]["projection.sqlite"]["sha256"] != metadata["previous_sha256"]):
                        raise ValueError("RETENTION_PAGE_DEPENDENCY_DEPTH_MISMATCH")
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
