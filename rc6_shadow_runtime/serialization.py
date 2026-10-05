"""Lossless bounded canonical JSON storage; no record is discarded.

Durable bytes, expansion, shape, CRC and stored/logical digests are guarded.
"""
import base64
import gzip
import hashlib
import io
import json
import time
import zlib

SCHEMA = "rc6.lossless-json-storage.v1"
CODEC = "GZIP_CANONICAL_ASCII_JSON_V1"
THRESHOLD = 256 * 1024
MAX_NODES = 32000000
MAX_DEPTH = 64


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str, allow_nan=False).encode()


def canonical_chunks(value, *, ensure_ascii=False):
    """Use native C JSON encoding per root member, avoiding a whole clone."""
    encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=ensure_ascii,
                               default=str, allow_nan=False)
    if isinstance(value, dict):
        yield b"{"
        for ordinal, key in enumerate(sorted(value)):
            if not isinstance(key, str):
                raise ValueError("SHADOW_STORAGE_STRING_KEY_REQUIRED")
            if ordinal: yield b","
            yield encoder.encode(key).encode(); yield b":"
            yield encoder.encode(value[key]).encode()
        yield b"}"
    elif isinstance(value, (list, tuple)):
        yield b"["
        for ordinal, item in enumerate(value):
            if ordinal: yield b","
            yield encoder.encode(item).encode()
        yield b"]"
    else:
        yield encoder.encode(value).encode()


def canonical_metrics(value, *, ensure_ascii=False, limit=None):
    accumulator, size = hashlib.sha256(), 0
    for raw in canonical_chunks(value, ensure_ascii=ensure_ascii):
        size += len(raw)
        if limit is not None and size > limit:
            raise ValueError("SHADOW_STORAGE_EXPANSION_CAPACITY_REACHED")
        accumulator.update(raw)
    return accumulator.hexdigest(), size


def _shape(value, *, memo=None):
    memo = {} if memo is None else memo
    active = set()
    def visit(node, depth=0):
        if depth > MAX_DEPTH:
            raise ValueError("SHADOW_STORAGE_COMPLEXITY_CAPACITY_REACHED")
        if not isinstance(node, (dict, list, tuple)): return 1, 0
        key = id(node)
        if key in active: raise ValueError("SHADOW_STORAGE_COMPLEXITY_CAPACITY_REACHED")
        if key in memo:
            count, height = memo[key]
            if count > MAX_NODES or depth+height > MAX_DEPTH:
                raise ValueError("SHADOW_STORAGE_COMPLEXITY_CAPACITY_REACHED")
            return count, height
        active.add(key); count, height = 1, 0
        try:
            if isinstance(node, dict):
                if any(not isinstance(name, str) for name in node): raise ValueError("SHADOW_STORAGE_STRING_KEY_REQUIRED")
                values = node.values()
            else: values = node
            for item in values:
                child_count, child_height = visit(item, depth+1)
                count += child_count; height = max(height, child_height+1)
                if count > MAX_NODES: raise ValueError("SHADOW_STORAGE_COMPLEXITY_CAPACITY_REACHED")
        finally:
            active.remove(key)
        if count >= 32 and len(memo) < 131072: memo[key] = count, height
        return count, height
    return visit(value)[0]


def _sha(value):
    return canonical_metrics(value)[0]


def encode_storage(value, *, durable_limit, expansion_limit):
    _shape(value)
    compressed, accumulator, size = io.BytesIO(), hashlib.sha256(), 0
    first = []
    chunks = iter(canonical_chunks(value, ensure_ascii=True))
    for raw in chunks:
        first.append(raw); size += len(raw)
        if size > expansion_limit:
            raise ValueError("SHADOW_STORAGE_EXPANSION_CAPACITY_REACHED")
        if size >= THRESHOLD: break
    else:
        if size > durable_limit: raise ValueError("SHADOW_STORAGE_DURABLE_CAPACITY_REACHED")
        return value
    with gzip.GzipFile(fileobj=compressed, mode="wb", mtime=0, compresslevel=1) as stream:
        for raw in first:
            accumulator.update(raw); stream.write(raw)
        first.clear()
        for raw in chunks:
            size += len(raw)
            if size > expansion_limit: raise ValueError("SHADOW_STORAGE_EXPANSION_CAPACITY_REACHED")
            accumulator.update(raw); stream.write(raw)
            if compressed.tell() > durable_limit:
                raise ValueError("SHADOW_STORAGE_DURABLE_CAPACITY_REACHED")
    result = {"schema": SCHEMA, "codec": CODEC, "payload": base64.b64encode(compressed.getvalue()).decode("ascii"),
              "logical_bytes": size, "logical_sha256": accumulator.hexdigest()}
    result["storage_sha256"] = _sha(result)
    if canonical_metrics(result)[1] > durable_limit:
        raise ValueError("SHADOW_STORAGE_DURABLE_CAPACITY_REACHED")
    return result


class PreparedStorage:
    """Serialize each immutable root section once within one publication.

    Only named root fields may change during quota/header preparation. Gzip's
    concatenated members still expand to exactly the same canonical JSON; this
    is no persistent cache and never changes logical values or their hashes.
    """
    def __init__(self, value, *, mutable, durable_limit, expansion_limit, cache=None, shape_memo=None):
        if not isinstance(value, dict): raise ValueError("SHADOW_STORAGE_ROOT_REQUIRED")
        _shape(value, memo=shape_memo)
        self.mutable = frozenset(mutable)
        self.durable_limit, self.expansion_limit = durable_limit, expansion_limit
        self.sections = {}
        cache = {} if cache is None else cache
        logical_size = 0
        for key, member in value.items():
            if key in self.mutable: continue
            cached = cache.get(id(member)) if isinstance(member, (dict, list)) else None
            if cached is not None and cached[0] is member:
                part = cached[1]
            else:
                raw = json.dumps(member, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                                 default=str, allow_nan=False).encode()
                part = gzip.compress(raw, mtime=0, compresslevel=1), len(raw)
                if isinstance(member, (dict, list)): cache[id(member)] = member, part
            logical_size += part[1]
            if logical_size > expansion_limit: raise ValueError("SHADOW_STORAGE_EXPANSION_CAPACITY_REACHED")
            self.sections[key] = part

    def _members(self, value):
        if set(value)-self.mutable != set(self.sections):
            raise ValueError("SHADOW_STORAGE_STATIC_FIELDS_CHANGED")
        yield gzip.compress(b"{", mtime=0, compresslevel=1), 1
        for ordinal, key in enumerate(sorted(value)):
            prefix = (b"," if ordinal else b"")+json.dumps(key, ensure_ascii=True).encode()+b":"
            yield gzip.compress(prefix, mtime=0, compresslevel=1), len(prefix)
            if key in self.mutable:
                _shape(value[key])
                raw = b"".join(canonical_chunks(value[key], ensure_ascii=True))
                yield gzip.compress(raw, mtime=0, compresslevel=1), len(raw)
            else:
                yield self.sections[key]
        yield gzip.compress(b"}", mtime=0, compresslevel=1), 1

    def _proof(self, members):
        accumulator, logical_size = hashlib.sha256(), 0
        for compressed, size in members:
            logical_size += size
            if logical_size > self.expansion_limit:
                raise ValueError("SHADOW_STORAGE_EXPANSION_CAPACITY_REACHED")
            with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
                while raw := stream.read(1024**2): accumulator.update(raw)
        return accumulator.hexdigest(), logical_size

    def metrics(self, value):
        return self._proof(self._members(value))

    def encode(self, value):
        members = list(self._members(value))
        logical_hash, logical_size = self._proof(members)
        if logical_size < THRESHOLD:
            if logical_size > self.durable_limit: raise ValueError("SHADOW_STORAGE_DURABLE_CAPACITY_REACHED")
            return value, logical_hash
        compressed = b"".join(member[0] for member in members)
        result = {"schema": SCHEMA, "codec": CODEC, "payload": base64.b64encode(compressed).decode("ascii"),
                  "logical_bytes": logical_size, "logical_sha256": logical_hash}
        result["storage_sha256"] = _sha(result)
        if canonical_metrics(result)[1] > self.durable_limit:
            raise ValueError("SHADOW_STORAGE_DURABLE_CAPACITY_REACHED")
        return result, logical_hash


def _storage_bytes(value, *, durable_limit, expansion_limit, retain, deadline=None):
    if (set(value) != {"schema", "codec", "payload", "logical_bytes", "logical_sha256", "storage_sha256"}
            or value["codec"] != CODEC or not isinstance(value["payload"], str)
            or type(value["logical_bytes"]) is not int or not 0 <= value["logical_bytes"] <= expansion_limit
            or len(value["payload"]) > durable_limit):
        raise ValueError("SHADOW_STORAGE_CONTRACT_INVALID")
    try:
        ascii_payload = value["payload"].encode("ascii")
        compressed = base64.b64decode(ascii_payload, validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError("SHADOW_STORAGE_BASE64_INVALID") from error
    accumulator, wrapper_size = hashlib.sha256(), 0
    encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str, allow_nan=False)
    for ordinal, key in enumerate(sorted(value)):
        prefix = (b"{" if ordinal == 0 else b",")+encoder.encode(key).encode()+b":"
        payload_size = len(ascii_payload)+2 if key == "payload" else len(encoder.encode(value[key]).encode())
        wrapper_size += len(prefix)+payload_size
    wrapper_size += 1
    if wrapper_size > durable_limit: raise ValueError("SHADOW_STORAGE_CONTRACT_INVALID")
    for ordinal, key in enumerate(sorted(set(value)-{"storage_sha256"})):
        accumulator.update((b"{" if ordinal == 0 else b",")+encoder.encode(key).encode()+b":")
        if key == "payload":
            accumulator.update(b'"'); accumulator.update(ascii_payload); accumulator.update(b'"')
        else: accumulator.update(encoder.encode(value[key]).encode())
    accumulator.update(b"}")
    if accumulator.hexdigest() != value["storage_sha256"]:
        raise ValueError("SHADOW_STORAGE_CONTRACT_INVALID")
    accumulator, size, output = hashlib.sha256(), 0, io.BytesIO() if retain else None
    try:
        cursor, pending = 0, b""
        stream = zlib.decompressobj(wbits=31)
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                raise ValueError("SHADOW_PROJECTION_QUERY_DEADLINE")
            if stream.eof:
                pending = stream.unused_data
                if not pending and cursor == len(compressed): break
                stream = zlib.decompressobj(wbits=31)
            if not pending:
                if cursor == len(compressed): raise ValueError("SHADOW_STORAGE_GZIP_INVALID")
                pending = compressed[cursor:cursor+65536]; cursor += len(pending)
            raw = stream.decompress(pending, min(1024**2, expansion_limit-size+1))
            pending = stream.unconsumed_tail
            size += len(raw)
            if size > expansion_limit or size > value["logical_bytes"]:
                raise ValueError("SHADOW_STORAGE_EXPANSION_CAPACITY_REACHED")
            accumulator.update(raw)
            if output is not None: output.write(raw)
    except (OSError, EOFError, zlib.error) as error:
        raise ValueError("SHADOW_STORAGE_GZIP_INVALID") from error
    if size != value["logical_bytes"] or accumulator.hexdigest() != value["logical_sha256"]:
        raise ValueError("SHADOW_STORAGE_LOGICAL_DIGEST_MISMATCH")
    return output.getvalue() if output is not None else None


def verify_storage_wire(value, *, durable_limit, expansion_limit, deadline=None):
    """CRC+canonical-byte SHA without materializing expanded logical JSON."""
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise ValueError("SHADOW_STORAGE_CONTRACT_INVALID")
    _storage_bytes(value, durable_limit=durable_limit, expansion_limit=expansion_limit, retain=False, deadline=deadline)
    return {"payload_digest": value["logical_sha256"], "logical_bytes": value["logical_bytes"], "storage_schema": SCHEMA}


def _loads(raw, *, share_subtrees):
    objects, strings = {}, {}
    def atom(value):
        if isinstance(value, dict): return ("dict", id(value))
        if isinstance(value, list): return ("list", tuple(atom(item) for item in value))
        return (type(value).__name__, value)
    def intern(value):
        if isinstance(value, str) and len(value) >= 12:
            if value in strings: return strings[value]
            if len(strings) < 262144: strings[value] = value
        if isinstance(value, list) and len(value) <= 64:
            normalized = [intern(item) for item in value]
            key = ("list", tuple(atom(item) for item in normalized))
            if key in objects: return objects[key]
            if len(objects) < 32768: objects[key] = normalized
            return normalized
        return value
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result: raise ValueError("SHADOW_DUPLICATE_JSON_KEY")
            result[key] = intern(value) if share_subtrees else value
        if share_subtrees and len(result) <= 64:
            key = ("dict", tuple((key, atom(value)) for key, value in result.items()))
            if key in objects: return objects[key]
            if len(objects) < 32768: objects[key] = result
        return result
    def constant(_): raise ValueError("SHADOW_NONFINITE_JSON")
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    finally:
        # Closure cycles must never retain a discarded immutable report.
        objects.clear(); strings.clear()


def decode_storage(value, *, durable_limit, expansion_limit, share_subtrees=False):
    if (isinstance(value, dict) and isinstance(value.get("schema"), str)
            and value["schema"].startswith("rc6.lossless-json-storage.") and value["schema"] != SCHEMA):
        raise ValueError("SHADOW_STORAGE_SCHEMA_UNSUPPORTED")
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        return value
    raw = _storage_bytes(value, durable_limit=durable_limit, expansion_limit=expansion_limit, retain=True)
    result = _loads(raw, share_subtrees=share_subtrees)
    del raw
    _shape(result)
    actual_digest, actual_size = canonical_metrics(result, ensure_ascii=True, limit=expansion_limit)
    if actual_size != value["logical_bytes"] or actual_digest != value["logical_sha256"]:
        raise ValueError("SHADOW_STORAGE_LOGICAL_DIGEST_MISMATCH")
    return result
