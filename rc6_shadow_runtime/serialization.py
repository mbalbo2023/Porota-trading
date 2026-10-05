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


def _shape(value):
    nodes = 0
    def visit(node, depth=0):
        nonlocal nodes
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise ValueError("SHADOW_STORAGE_COMPLEXITY_CAPACITY_REACHED")
        if isinstance(node, dict):
            for key, item in node.items():
                if not isinstance(key, str): raise ValueError("SHADOW_STORAGE_STRING_KEY_REQUIRED")
                visit(item, depth+1)
        elif isinstance(node, (list, tuple)):
            for item in node: visit(item, depth+1)
    visit(value)
    return nodes


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
    with gzip.GzipFile(fileobj=compressed, mode="wb", mtime=0) as stream:
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


def _storage_bytes(value, *, durable_limit, expansion_limit, retain, deadline=None):
    if (set(value) != {"schema", "codec", "payload", "logical_bytes", "logical_sha256", "storage_sha256"}
            or value["codec"] != CODEC or not isinstance(value["payload"], str)
            or canonical_metrics(value)[1] > durable_limit
            or type(value["logical_bytes"]) is not int or not 0 <= value["logical_bytes"] <= expansion_limit
            or value["storage_sha256"] != _sha({key: item for key, item in value.items() if key != "storage_sha256"})):
        raise ValueError("SHADOW_STORAGE_CONTRACT_INVALID")
    try:
        compressed = base64.b64decode(value["payload"], validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError("SHADOW_STORAGE_BASE64_INVALID") from error
    accumulator, size, output = hashlib.sha256(), 0, io.BytesIO() if retain else None
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
            while True:
                if deadline is not None and time.monotonic() >= deadline:
                    raise ValueError("SHADOW_PROJECTION_QUERY_DEADLINE")
                raw = stream.read(min(1024**2, expansion_limit-size+1))
                if not raw: break
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
