"""Lossless packed canonical-JSON templates, scoped to one publication/read.

This module does not publish generations or operate an archive. Every packet
has an original compressed-byte digest and complete gzip CRC. The complete
expanded canonical stream, including every repeated instance, has a distinct
length and SHA256. Verified bytes never become mutable shared JSON objects.
"""
from dataclasses import dataclass
import base64
import gzip
import hashlib
import json
import re
import struct
import time
import zlib


SCHEMA = "rc6.lossless-json-storage.v2"
CODEC = "GZIP_PACKED_TEMPLATE_CANONICAL_ASCII_JSON_V2"
ARRAY_CODEC = "GZIP_BIG_ENDIAN_UINT32_V1"
PACK_TARGET = 65536
MAX_ENTRIES = 262144
MAX_INSTANCES = 262144
MAX_REFERENCES = 1048576
MAX_BINDINGS = 32000000
MAX_PACKETS = 16384
MAX_UNIQUE_BYTES = 256 * 1024**2
MAX_ENTRY_BYTES = 64 * 1024**2
MAX_TEMPLATE_MARKERS = 262144
MAX_MARKER_OCCURRENCES = 1048576
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_HEADER = b"RC6P2"
_FIELDS = frozenset({"stages", "rank_components", "pipeline", "provenance", "basis", "policy", "reasons", "reason_codes"})
_VOLATILE = re.compile(
    rb'(?P<prefix>"(?:[A-Za-z_]*(?:_at|_seconds)|as_of|last_as_of|sequence|generation_id|'
    rb'report_digest|checkpoint_digest|logical_sha256|storage_sha256|sha256|payload)":)'
    rb'(?P<value>"(?:[^"\\]|\\.)*"|-?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?|true|false|null)')
_KEYS = {"schema", "codec", "packets", "templates_count", "literals_count", "directory", "instances",
         "bindings", "references", "logical_bytes", "logical_sha256", "storage_sha256"}


def _canonical(value, *, ascii=True):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=ascii,
                      default=str, allow_nan=False).encode()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _guard(deadline):
    if deadline is not None and time.monotonic() >= deadline:
        raise ValueError("SHADOW_PROJECTION_QUERY_DEADLINE")


def _integer(value, maximum, *, positive=False):
    if type(value) is not int or not (int(positive) <= value <= maximum):
        raise ValueError("SHADOW_STORAGE_CONTRACT_INVALID")
    return value


def _digest(value):
    if not isinstance(value, str) or _HEX.fullmatch(value) is None:
        raise ValueError("SHADOW_STORAGE_CONTRACT_INVALID")
    return value


def _compress(raw):
    # zlib's gzip encoder is portable to decode; restore never uses this path.
    return gzip.compress(raw, compresslevel=1, mtime=0)


def _record(raw, *, count=None):
    compressed = _compress(raw)
    result = {"sha256": _sha(compressed), "payload": base64.b64encode(compressed).decode("ascii")}
    if count is not None:
        result.update(codec=ARRAY_CODEC, count=count)
    return result


def _compressed(record, *, limit, deadline):
    _guard(deadline)
    if not isinstance(record, dict) or not isinstance(record.get("payload"), str):
        raise ValueError("SHADOW_STORAGE_CONTRACT_INVALID")
    _digest(record.get("sha256"))
    if len(record["payload"]) > limit:
        raise ValueError("SHADOW_STORAGE_DURABLE_CAPACITY_REACHED")
    try:
        ascii_payload = record["payload"].encode("ascii")
        raw = base64.b64decode(ascii_payload, validate=True)
    except (ValueError, TypeError, UnicodeError) as error:
        raise ValueError("SHADOW_STORAGE_BASE64_INVALID") from error
    if base64.b64encode(raw) != ascii_payload or _sha(raw) != record["sha256"]:
        raise ValueError("SHADOW_STORAGE_COMPRESSED_DIGEST_MISMATCH")
    return raw


def _unique_limit(expansion_limit):
    # A five-byte marker can replace the one-byte canonical integer zero.
    # Bound stored templates independently from their expanded JSON domain.
    return min(MAX_UNIQUE_BYTES, 5 * expansion_limit)


def _inflate(raw, *, maximum, deadline):
    stream, output, size = zlib.decompressobj(wbits=31), [], 0
    try:
        for offset in range(0, len(raw), 65536):
            _guard(deadline)
            pending = raw[offset:offset+65536]
            while pending:
                _guard(deadline)
                chunk = stream.decompress(pending, min(1024**2, maximum-size+1))
                size += len(chunk)
                if size > maximum:
                    raise ValueError("SHADOW_STORAGE_EXPANSION_CAPACITY_REACHED")
                output.append(chunk)
                pending = stream.unconsumed_tail
                if stream.eof:
                    if stream.unused_data or pending or offset+65536 < len(raw):
                        raise ValueError("SHADOW_STORAGE_GZIP_INVALID")
                    break
        if not stream.eof:
            raise ValueError("SHADOW_STORAGE_GZIP_INVALID")
    except zlib.error as error:
        raise ValueError("SHADOW_STORAGE_GZIP_INVALID") from error
    _guard(deadline)
    return b"".join(output)


def _array(record, *, maximum_count, width, durable_limit, deadline):
    if (not isinstance(record, dict) or set(record) != {"codec", "count", "sha256", "payload"}
            or record["codec"] != ARRAY_CODEC):
        raise ValueError("SHADOW_STORAGE_CONTRACT_INVALID")
    count = _integer(record["count"], maximum_count)
    compressed = _compressed(record, limit=durable_limit, deadline=deadline)
    raw = _inflate(compressed, maximum=count*width, deadline=deadline)
    if len(raw) != count*width:
        raise ValueError("SHADOW_STORAGE_ARRAY_LENGTH_MISMATCH")
    return raw, count


def _root_volatile(name):
    return (name.endswith("_at") or name.endswith("_seconds") or name in
        {"as_of", "last_as_of", "sequence", "generation_id", "cross_payload_hashes", "report_digest",
         "checkpoint_digest", "evidence_retention", "logical_sha256", "storage_sha256", "sha256", "payload"})


@dataclass(frozen=True)
class Capture:
    template: bytes
    literals: tuple


def _slots(template, *, deadline=None, maximum=MAX_TEMPLATE_MARKERS):
    """Return exact marker positions and require a dense, fully used slot set."""
    positions, used, start = [], set(), 0
    while True:
        _guard(deadline)
        marker = template.find(b"\0", start)
        if marker < 0:
            break
        if marker+5 > len(template):
            raise ValueError("SHADOW_STORAGE_TEMPLATE_MARKER_INVALID")
        index = struct.unpack_from("!I", template, marker+1)[0]
        if index >= MAX_BINDINGS:
            raise ValueError("SHADOW_STORAGE_COMPLEXITY_CAPACITY_REACHED")
        if len(positions) >= min(MAX_TEMPLATE_MARKERS, maximum):
            raise ValueError("SHADOW_STORAGE_MARKER_CAPACITY_REACHED")
        positions.append((marker, index)); used.add(index); start = marker+5
    count = max(used, default=-1)+1
    if len(used) != count:
        raise ValueError("SHADOW_STORAGE_UNUSED_LITERAL_SLOT")
    return tuple(positions), count


def _expanded(capture, *, expansion_limit, deadline=None, _verified_slots=None):
    positions, count = (_slots(capture.template, deadline=deadline)
                        if _verified_slots is None else _verified_slots)
    if count != len(capture.literals):
        raise ValueError("SHADOW_STORAGE_TEMPLATE_BINDING_MISMATCH")
    if not positions:
        if len(capture.template) > expansion_limit:
            raise ValueError("SHADOW_STORAGE_EXPANSION_CAPACITY_REACHED")
        _guard(deadline)
        return capture.template
    parts, start, size = [], 0, len(capture.template)-5*len(positions)
    for marker, index in positions:
        _guard(deadline)
        literal = capture.literals[index]
        size += len(literal)
        if size > expansion_limit:
            raise ValueError("SHADOW_STORAGE_EXPANSION_CAPACITY_REACHED")
        parts.extend((capture.template[start:marker], literal)); start = marker+5
    parts.append(capture.template[start:])
    if size > expansion_limit:
        raise ValueError("SHADOW_STORAGE_EXPANSION_CAPACITY_REACHED")
    return b"".join(parts)


def _combined(captures):
    parts, literals = [], []
    for capture in captures:
        offset = len(literals)
        if not offset:
            parts.append(capture.template)
        else:
            positions, count = _slots(capture.template)
            if count != len(capture.literals):
                raise ValueError("SHADOW_STORAGE_TEMPLATE_BINDING_MISMATCH")
            start = 0
            for marker, index in positions:
                parts.extend((capture.template[start:marker], b"\0"+struct.pack("!I", offset+index)))
                start = marker+5
            parts.append(capture.template[start:])
        literals.extend(capture.literals)
    if len(literals) > MAX_BINDINGS:
        raise ValueError("SHADOW_STORAGE_COMPLEXITY_CAPACITY_REACHED")
    return Capture(b"".join(parts), tuple(literals))


class _CaptureBuffer:
    """Keep the legacy token cuts without allocating a Capture per token.

    The target remains soft: a single larger token occupies its own chunk,
    subject to the existing entry and expansion guards. Only immutable bytes
    escape a flush; the mutable accumulator belongs to one capture call.
    """
    def __init__(self):
        self.result, self.template, self.literals = [], bytearray(), []

    def flush(self):
        if self.template:
            if len(self.literals) > MAX_BINDINGS:
                raise ValueError("SHADOW_STORAGE_COMPLEXITY_CAPACITY_REACHED")
            self.result.append(Capture(bytes(self.template), tuple(self.literals)))
            self.template.clear(); self.literals.clear()

    def _room(self, size):
        if self.template and len(self.template)+size > PACK_TARGET:
            self.flush()

    def append(self, raw):
        self._room(len(raw))
        self.template.extend(raw)

    def bind(self, literal):
        self._room(5)
        self.template.extend(b"\0"+struct.pack("!I", len(self.literals)))
        self.literals.append(literal)

    def boundary(self, capture):
        self.flush()
        self.result.append(capture)


class _CaptureBuilder:
    """Strong references and immutable byte plans within this publication only."""
    def __init__(self, value, *, cache=None):
        self.objects, self.incoming = {}, {}
        self.cache = {} if cache is None else cache
        self.scalars = {}
        self._count(value)

    def _count(self, value):
        if not isinstance(value, (dict, list, tuple)):
            return
        key = id(value)
        self.incoming[key] = self.incoming.get(key, 0)+1
        if key in self.objects:
            return
        self.objects[key] = value
        for child in value.values() if isinstance(value, dict) else value:
            # Scalar leaves do not contribute a container identity or edge.
            # Keep alias arrivals and strong references in the recursive path.
            if isinstance(child, (dict, list, tuple)):
                self._count(child)

    def _scalar(self, value):
        if type(value) not in (type(None), bool, int, float, str):
            return _canonical(value)
        # Python equates negative zero with positive zero; its JSON lexeme is
        # distinct and must remain distinct even inside a local byte cache.
        key = type(value), value.hex() if type(value) is float else value
        if key not in self.scalars:
            self.scalars[key] = _canonical(value)
        return self.scalars[key]

    def _named(self, value):
        literals, indices = [], {}
        def replace(match):
            key = match["prefix"], match["value"]
            if key not in indices:
                indices[key] = len(literals); literals.append(match["value"])
            return match["prefix"]+b"\0"+struct.pack("!I", indices[key])
        return Capture(_VOLATILE.sub(replace, _canonical(value)), tuple(literals))

    def _append(self, value, name, buffer, *, root=False):
        if isinstance(value, (dict, list, tuple)) and not root and (
                self.incoming.get(id(value), 0) >= 2 or name in _FIELDS):
            cached = self.cache.get(id(value))
            if cached is None or cached[0] is not value:
                capture = self._named(value)
                self.cache[id(value)] = value, capture
            else:
                capture = cached[1]
            if len(capture.template) >= 64:
                buffer.boundary(capture); return
        if isinstance(value, dict):
            buffer.append(b"{")
            for ordinal, key in enumerate(sorted(value)):
                buffer.append((b"," if ordinal else b"")+self._scalar(key)+b":")
                child = value[key]
                if _root_volatile(key):
                    buffer.bind(_canonical(child))
                else:
                    self._append(child, key, buffer)
            buffer.append(b"}")
        elif isinstance(value, (list, tuple)):
            buffer.append(b"[")
            for ordinal, child in enumerate(value):
                if ordinal:
                    buffer.append(b",")
                self._append(child, name, buffer)
            buffer.append(b"]")
        else:
            buffer.append(self._scalar(value))

    def capture(self, value, name=""):
        if _root_volatile(name):
            return (Capture(b"\0"+b"\0"*4, (_canonical(value),)),)
        buffer = _CaptureBuffer()
        self._append(value, name, buffer, root=True)
        buffer.flush()
        return tuple(buffer.result)


class PreparedPackedStorage:
    """Freeze static root fields once; mutable root headers are captured fresh."""
    def __init__(self, value, *, mutable=(), durable_limit, expansion_limit, cache=None, shape_memo=None):
        from .serialization import _shape
        if not isinstance(value, dict):
            raise ValueError("SHADOW_STORAGE_ROOT_REQUIRED")
        _shape(value, memo=shape_memo)
        self.mutable = frozenset(mutable)
        self.durable_limit, self.expansion_limit = durable_limit, expansion_limit
        builder = _CaptureBuilder(value, cache=cache)
        self.sections = {key: builder.capture(member, key) for key, member in value.items() if key not in self.mutable}
        self.expanded = {}

    def _captures(self, value):
        if set(value)-self.mutable != set(self.sections):
            raise ValueError("SHADOW_STORAGE_STATIC_FIELDS_CHANGED")
        yield Capture(b"{", ())
        for ordinal, key in enumerate(sorted(value)):
            yield Capture((b"," if ordinal else b"")+_canonical(key)+b":", ())
            if key in self.mutable:
                from .serialization import _shape
                _shape(value[key])
                yield from _CaptureBuilder(value[key]).capture(value[key], key)
            else:
                yield from self.sections[key]
        yield Capture(b"}", ())

    def _proof(self, captures):
        accumulator, size = hashlib.sha256(), 0
        for capture in captures:
            if capture not in self.expanded:
                self.expanded[capture] = _expanded(capture, expansion_limit=self.expansion_limit)
            raw = self.expanded[capture]
            size += len(raw)
            if size > self.expansion_limit:
                raise ValueError("SHADOW_STORAGE_EXPANSION_CAPACITY_REACHED")
            accumulator.update(raw)
        return accumulator.hexdigest(), size

    def metrics(self, value):
        return self._proof(self._captures(value))

    def encode(self, value):
        from .serialization import THRESHOLD
        captures = tuple(self._captures(value))
        logical_sha, logical_bytes = self._proof(captures)
        if logical_bytes < THRESHOLD:
            if logical_bytes > self.durable_limit:
                raise ValueError("SHADOW_STORAGE_DURABLE_CAPACITY_REACHED")
            return value, logical_sha
        result = _encode_captures(captures, logical_sha=logical_sha, logical_bytes=logical_bytes,
                                  durable_limit=self.durable_limit, expansion_limit=self.expansion_limit)
        return result, logical_sha


def _encode_captures(captures, *, logical_sha, logical_bytes, durable_limit, expansion_limit):
    templates, template_indices, literals, literal_indices = [], {}, [], {}
    instances, instance_indices, bindings, references = [], {}, [], []
    for capture in captures:
        if capture.template not in template_indices:
            template_indices[capture.template] = len(templates); templates.append(capture.template)
        bound = []
        for literal in capture.literals:
            if literal not in literal_indices:
                literal_indices[literal] = len(literals); literals.append(literal)
            bound.append(literal_indices[literal])
        key = template_indices[capture.template], tuple(bound)
        if key not in instance_indices:
            instance_indices[key] = len(instances)
            instances.append((key[0], len(bindings), len(bound)))
            bindings.extend(bound)
        references.append(instance_indices[key])
    if (len(templates)+len(literals) > MAX_ENTRIES or len(instances) > MAX_INSTANCES
            or len(bindings) > MAX_BINDINGS or len(references) > MAX_REFERENCES):
        raise ValueError("SHADOW_STORAGE_COMPLEXITY_CAPACITY_REACHED")
    marker_count = 0
    for template in templates:
        positions, _ = _slots(template, maximum=MAX_MARKER_OCCURRENCES-marker_count)
        marker_count += len(positions)
    packets, directory, packet = [], [], bytearray()
    kind, ordinal, total_raw = 0, 0, 0
    def flush():
        nonlocal ordinal
        if packet:
            packets.append(_record(bytes(packet))); packet.clear(); ordinal += 1
            if len(packets) > MAX_PACKETS:
                raise ValueError("SHADOW_STORAGE_COMPLEXITY_CAPACITY_REACHED")
    for group_kind, entries in enumerate((templates, literals)):
        if group_kind != kind:
            flush(); kind = group_kind
        for entry in entries:
            if not 0 < len(entry) <= MAX_ENTRY_BYTES:
                raise ValueError("SHADOW_STORAGE_ENTRY_CAPACITY_REACHED")
            if packet and len(packet)+len(entry) > PACK_TARGET:
                flush()
            if not packet:
                packet.extend(_HEADER+bytes([kind])+struct.pack("!I", ordinal))
            directory.append((len(packets), len(packet), len(entry)))
            packet.extend(entry); total_raw += len(entry)
            if total_raw > _unique_limit(expansion_limit):
                raise ValueError("SHADOW_STORAGE_EXPANSION_CAPACITY_REACHED")
    flush()
    result = {"schema": SCHEMA, "codec": CODEC, "packets": packets,
        "templates_count": len(templates), "literals_count": len(literals),
        "directory": _record(b"".join(struct.pack("!III", *row) for row in directory), count=len(directory)),
        "instances": _record(b"".join(struct.pack("!III", *row) for row in instances), count=len(instances)),
        "bindings": _record(b"".join(struct.pack("!I", row) for row in bindings), count=len(bindings)),
        "references": _record(b"".join(struct.pack("!I", row) for row in references), count=len(references)),
        "logical_bytes": logical_bytes, "logical_sha256": logical_sha}
    canonical = _canonical(result, ascii=False)
    result["storage_sha256"] = _sha(canonical)
    if len(canonical) + len(_canonical({"storage_sha256": result["storage_sha256"]})) - 1 > durable_limit:
        raise ValueError("SHADOW_STORAGE_DURABLE_CAPACITY_REACHED")
    return result


def encode_packed_storage(value, *, durable_limit, expansion_limit):
    if not isinstance(value, dict):
        from .serialization import encode_storage
        return encode_storage(value, durable_limit=durable_limit, expansion_limit=expansion_limit)
    prepared = PreparedPackedStorage(value, durable_limit=durable_limit, expansion_limit=expansion_limit)
    return prepared.encode(value)[0]


def unpack_wire(value, *, durable_limit, expansion_limit, retain=False, deadline=None):
    """Fully verify wire and ordered expansion; return bytes only for full parse."""
    _guard(deadline)
    if not isinstance(value, dict) or set(value) != _KEYS or value.get("schema") != SCHEMA or value.get("codec") != CODEC:
        raise ValueError("SHADOW_STORAGE_CONTRACT_INVALID")
    _integer(value["logical_bytes"], expansion_limit)
    _digest(value["logical_sha256"]); _digest(value["storage_sha256"])
    without_digest = {key: member for key, member in value.items() if key != "storage_sha256"}
    canonical = _canonical(without_digest, ascii=False)
    if _sha(canonical) != value["storage_sha256"]:
        raise ValueError("SHADOW_STORAGE_CONTRACT_INVALID")
    if len(canonical) + len(_canonical({"storage_sha256": value["storage_sha256"]})) - 1 > durable_limit:
        raise ValueError("SHADOW_STORAGE_DURABLE_CAPACITY_REACHED")
    del canonical, without_digest
    templates_count = _integer(value["templates_count"], MAX_ENTRIES, positive=True)
    literals_count = _integer(value["literals_count"], MAX_ENTRIES)
    if templates_count+literals_count > MAX_ENTRIES:
        raise ValueError("SHADOW_STORAGE_COMPLEXITY_CAPACITY_REACHED")
    packets = value["packets"]
    if not isinstance(packets, list) or not 0 < len(packets) <= MAX_PACKETS:
        raise ValueError("SHADOW_STORAGE_CONTRACT_INVALID")
    decoded_packets, compressed_hashes, unique_bytes = [], set(), 0
    for ordinal, packet in enumerate(packets):
        _guard(deadline)
        if not isinstance(packet, dict) or set(packet) != {"sha256", "payload"}:
            raise ValueError("SHADOW_STORAGE_CONTRACT_INVALID")
        compressed = _compressed(packet, limit=durable_limit, deadline=deadline)
        if packet["sha256"] in compressed_hashes:
            raise ValueError("SHADOW_STORAGE_DUPLICATE_PACKET")
        compressed_hashes.add(packet["sha256"])
        raw = _inflate(compressed, maximum=_unique_limit(expansion_limit)-unique_bytes+10, deadline=deadline)
        if (len(raw) <= 10 or raw[:5] != _HEADER or raw[5] not in (0, 1)
                or struct.unpack_from("!I", raw, 6)[0] != ordinal):
            raise ValueError("SHADOW_STORAGE_PACKET_HEADER_INVALID")
        unique_bytes += len(raw)-10
        if unique_bytes > _unique_limit(expansion_limit):
            raise ValueError("SHADOW_STORAGE_EXPANSION_CAPACITY_REACHED")
        decoded_packets.append(raw)
    raw, count = _array(value["directory"], maximum_count=MAX_ENTRIES, width=12,
                         durable_limit=durable_limit, deadline=deadline)
    if count != templates_count+literals_count:
        raise ValueError("SHADOW_STORAGE_DIRECTORY_INVALID")
    entries, cursors = [], [10]*len(decoded_packets)
    previous_packet = -1
    for ordinal, (packet, offset, length) in enumerate(struct.iter_unpack("!III", raw)):
        _guard(deadline)
        if (packet >= len(decoded_packets) or packet < previous_packet or not 0 < length <= MAX_ENTRY_BYTES
                or offset != cursors[packet] or offset+length > len(decoded_packets[packet])
                or decoded_packets[packet][5] != int(ordinal >= templates_count)):
            raise ValueError("SHADOW_STORAGE_DIRECTORY_INVALID")
        entries.append(decoded_packets[packet][offset:offset+length])
        cursors[packet] += length; previous_packet = packet
    if any(cursor != len(packet) for cursor, packet in zip(cursors, decoded_packets)):
        raise ValueError("SHADOW_STORAGE_UNUSED_PACKET_BYTES")
    del decoded_packets
    templates, literals = entries[:templates_count], entries[templates_count:]
    if len(set(templates)) != len(templates) or len(set(literals)) != len(literals):
        raise ValueError("SHADOW_STORAGE_DUPLICATE_ENTRY")
    slots, marker_count = [], 0
    for template in templates:
        positions = _slots(template, deadline=deadline, maximum=MAX_MARKER_OCCURRENCES-marker_count)
        slots.append(positions); marker_count += len(positions[0])
    raw_instances, instance_count = _array(value["instances"], maximum_count=MAX_INSTANCES, width=12,
                                           durable_limit=durable_limit, deadline=deadline)
    if not instance_count:
        raise ValueError("SHADOW_STORAGE_CONTRACT_INVALID")
    instance_rows, binding_count = [], 0
    used_templates = set()
    for template, start, count in struct.iter_unpack("!III", raw_instances):
        _guard(deadline)
        if (template >= templates_count or start != binding_count or count != slots[template][1]
                or binding_count+count > MAX_BINDINGS):
            raise ValueError("SHADOW_STORAGE_TEMPLATE_BINDING_MISMATCH")
        instance_rows.append((template, start, count)); used_templates.add(template); binding_count += count
    if (used_templates != set(range(templates_count)) or not isinstance(value["bindings"], dict)
            or type(value["bindings"].get("count")) is not int or value["bindings"]["count"] != binding_count):
        raise ValueError("SHADOW_STORAGE_UNUSED_ENTRY")
    bindings_raw, _ = _array(value["bindings"], maximum_count=MAX_BINDINGS, width=4,
                              durable_limit=durable_limit, deadline=deadline)
    used_literals, expanded, expanded_bytes, seen_instances = set(), [], 0, set()
    for template, start, count in instance_rows:
        _guard(deadline)
        bound = tuple(struct.unpack_from("!I", bindings_raw, 4*position)[0] for position in range(start, start+count))
        if any(index >= literals_count for index in bound) or (template, bound) in seen_instances:
            raise ValueError("SHADOW_STORAGE_TEMPLATE_BINDING_MISMATCH")
        seen_instances.add((template, bound)); used_literals.update(bound)
        raw = _expanded(Capture(templates[template], tuple(literals[index] for index in bound)),
                        expansion_limit=min(expansion_limit, value["logical_bytes"])-expanded_bytes,
                        deadline=deadline, _verified_slots=slots[template])
        expanded_bytes += len(raw); expanded.append(raw)
    if used_literals != set(range(literals_count)):
        raise ValueError("SHADOW_STORAGE_UNUSED_ENTRY")
    refs_raw, reference_count = _array(value["references"], maximum_count=MAX_REFERENCES, width=4,
                                      durable_limit=durable_limit, deadline=deadline)
    if not reference_count:
        raise ValueError("SHADOW_STORAGE_CONTRACT_INVALID")
    accumulator, logical_bytes, used_instances = hashlib.sha256(), 0, set()
    output = [] if retain else None
    for ordinal, in struct.iter_unpack("!I", refs_raw):
        _guard(deadline)
        if ordinal >= instance_count:
            raise ValueError("SHADOW_STORAGE_REFERENCE_INVALID")
        raw = expanded[ordinal]
        logical_bytes += len(raw); used_instances.add(ordinal)
        if logical_bytes > expansion_limit or logical_bytes > value["logical_bytes"]:
            raise ValueError("SHADOW_STORAGE_EXPANSION_CAPACITY_REACHED")
        accumulator.update(raw)
        if output is not None:
            output.append(raw)
    if (logical_bytes != value["logical_bytes"] or accumulator.hexdigest() != value["logical_sha256"]
            or used_instances != set(range(instance_count))):
        raise ValueError("SHADOW_STORAGE_LOGICAL_DIGEST_MISMATCH")
    _guard(deadline)
    return b"".join(output) if output is not None else None


def verify_wire(value, *, durable_limit, expansion_limit, deadline=None):
    unpack_wire(value, durable_limit=durable_limit, expansion_limit=expansion_limit, deadline=deadline)
    return {"payload_digest": value["logical_sha256"], "logical_bytes": value["logical_bytes"], "storage_schema": SCHEMA}


def envelope_components(envelope):
    """Create independently framed source gzip members with exact JSON bytes."""
    value = envelope.get("payload") if isinstance(envelope, dict) else None
    if (not isinstance(envelope, dict) or set(envelope) != {"digest", "payload"}
            or not isinstance(value, dict) or value.get("schema") != SCHEMA):
        return (_compress(_canonical(envelope)),)
    parts = [b'{"digest":'+_canonical(envelope["digest"])+b',"payload":{']
    for ordinal, key in enumerate(sorted(value)):
        prefix = (b"," if ordinal else b"")+_canonical(key)+b":"
        if key == "packets":
            parts.append(prefix+b"[")
            for position, packet in enumerate(value[key]):
                parts.append((b"," if position else b"")+_canonical(packet))
            parts.append(b"]")
        else:
            parts.append(prefix+_canonical(value[key]))
    parts.append(b"}}")
    return tuple(_compress(part) for part in parts)
