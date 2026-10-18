"""Exclusive bounded private files. Trading inputs are never writable outputs."""
import fcntl
import gzip
import hashlib
import json
import os
import re
import sys
import time
import zlib
from pathlib import Path
import tempfile

from rc6_dynamic_universe.common import digest


class _FrozenEmptyList(list):
    """An empty JSON array which cannot be changed through normal list APIs."""
    def _readonly(self,*args,**kwargs):
        raise TypeError("IMMUTABLE_JSON_EVIDENCE")
    __setitem__=__delitem__=__iadd__=__imul__=_readonly
    append=clear=extend=insert=pop=remove=reverse=sort=_readonly
    __init__=_readonly
    def __copy__(self):return self
    def __deepcopy__(self,memo):return self


class FrozenJSONDict(dict):
    """Private scalar/empty-array evidence with a bounded encoding-size hint.

    Only missing-field summaries use this type. Other field observations and
    all mutable quote/identity envelopes remain independent ordinary objects.
    """
    __slots__=("_canonical_json",)
    def __init__(self,payload):
        if hasattr(self,"_canonical_json"):
            raise TypeError("IMMUTABLE_JSON_EVIDENCE")
        values={}
        for key,value in payload.items():
            if type(key) is not str:
                raise TypeError("IMMUTABLE_JSON_SCALAR_KEYS_REQUIRED")
            if type(value) is list and not value:
                value=list.__new__(_FrozenEmptyList)
            elif value is not None and type(value) not in (str,int,float,bool):
                raise TypeError("IMMUTABLE_JSON_SCALARS_OR_EMPTY_ARRAYS_REQUIRED")
            values[key]=value
        encoded=json.dumps(values,sort_keys=True,separators=(",",":"),allow_nan=False)
        dict.__init__(self,values)
        object.__setattr__(self,"_canonical_json",encoded)
    def _readonly(self,*args,**kwargs):
        raise TypeError("IMMUTABLE_JSON_EVIDENCE")
    __setitem__=__delitem__=__ior__=__setattr__=__delattr__=_readonly
    clear=pop=popitem=setdefault=update=_readonly
    def __copy__(self):return self
    def __deepcopy__(self,memo):return self

    @classmethod
    def _from_owned_metadata_tree(cls, payload):
        """Freeze one small missing-data summary, including its provenance.

        Only scalar leaves, empty arrays and already frozen children are
        supported. This is a per-call private view, never a cross-tick cache.
        """
        remaining = [256]
        def freeze(value):
            remaining[0] -= 1
            if remaining[0] < 0:
                raise ValueError("IMMUTABLE_METADATA_TREE_BUDGET")
            if type(value) is cls:
                return value
            if type(value) is dict:
                if any(type(key) is not str for key in value):
                    raise TypeError("IMMUTABLE_JSON_SCALAR_KEYS_REQUIRED")
                values = {key: freeze(item) for key,item in value.items()}
                result = dict.__new__(cls)
                dict.__init__(result, values)
                object.__setattr__(result, '_canonical_json', json.dumps(values,
                    sort_keys=True,separators=(',',':'),allow_nan=False))
                return result
            if type(value) is list and not value:
                return list.__new__(_FrozenEmptyList)
            if value is None or type(value) in (str,int,float,bool):
                return value
            raise TypeError("IMMUTABLE_METADATA_LEAVES_REQUIRED")
        return freeze(payload)


def _preopen_scalar_decoder():
    """Intern only exact scalar metrics within one immutable preopen read.

    The on-disk envelope remains unchanged. No cross-generation cache exists,
    and the canonical digest below is calculated from the decoded values.
    Type-tagged keys preserve bool/int and signed-zero distinctions.
    """
    shared = {}
    charged = 0
    def decode(value):
        nonlocal charged
        if not ("status" in value and "value" in value) or len(value) > 16:
            return value
        key = []
        for name, item in value.items():
            kind = type(item)
            if len(name) > 128 or (kind is str and len(item) > 4096):
                return value
            if item is None or kind in (str, int, bool):
                key.append((name, kind.__name__, item))
            elif kind is float:
                key.append((name, "float", item.hex()))
            elif kind is list and not item:
                key.append((name, "empty_array", None))
            else:
                return value
        key = tuple(sorted(key))
        prior = shared.get(key)
        if prior is not None:
            return prior
        if len(shared) < 4096 and charged < 1024**2:
            frozen = FrozenJSONDict(value)
            cost = len(frozen._canonical_json) + 256 + 96 * len(key)
            if charged + cost <= 1024**2:
                value = frozen
                shared[key] = value
                charged += cost
        return value
    return decode


def streamed_digest(payload):
    """Canonical SHA without a document-sized string and UTF-8 copy."""
    encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"),
                               default=str, allow_nan=False)
    hasher = hashlib.sha256()
    for chunk in _canonical_chunks(encoder, payload):
        hasher.update(chunk)
    return hasher.hexdigest()


def checkpoint_metrics(payload):
    """Hash and count the exact performance canonical JSON in bounded pieces.

    The checkpoint signature excludes only its top-level signature field.
    Its byte budget includes that field. A signed size is also returned so
    a new signature can be installed without serializing the whole tree a
    second time. This uses ensure_ascii=False, unlike the evidence envelope.
    No encoded content or object survives this call.
    """
    encoder = json.JSONEncoder(sort_keys=True, ensure_ascii=False,
                               separators=(",", ":"), default=str, allow_nan=False)
    body = {key: value for key, value in payload.items() if key != "checkpoint_sha256"}
    hasher, body_bytes = hashlib.sha256(), 0
    for fragment in _canonical_fragments(encoder, body):
        # Even a large scalar stays bounded while converting to UTF-8.
        for offset in range(0, len(fragment), 16384):
            block = fragment[offset:offset + 16384].encode("utf-8")
            body_bytes += len(block)
            hasher.update(block)
    signature = hasher.hexdigest()
    overhead = len(b'"checkpoint_sha256":') + (1 if body else 0)
    actual = body_bytes
    if "checkpoint_sha256" in payload:
        # A malformed signature still has its exact original encoded size;
        # callers independently compare its value with the computed hash.
        actual += overhead + len(encoder.encode(payload["checkpoint_sha256"]).encode("utf-8"))
    return {"sha256": signature, "canonical_bytes": actual,
            "signed_canonical_bytes": body_bytes + overhead + 66}


def _small_json_tree_python(value, maximum=65536, nodes=4096):
    """Bound one C-encoded record without recursively walking every key."""
    pending = [value]
    while pending:
        item = pending.pop()
        nodes -= 1
        if nodes < 0:
            return False
        kind = type(item)
        if kind is FrozenJSONDict:
            maximum -= len(item._canonical_json)
        elif kind is str:
            maximum -= len(json.encoder.encode_basestring_ascii(item))
        elif item is None or kind is bool:
            maximum -= 5
        elif kind is int:
            maximum -= max(1, item.bit_length()) + 3
        elif kind is float:
            maximum -= 64
        elif kind is dict:
            if len(item) > 256:
                return False
            maximum -= 2 + 2 * len(item)
            # String keys are overwhelmingly common. Their exact C escape
            # length avoids a pessimistic 12x bound splitting ordinary rows.
            for key in item:
                if type(key) is str:
                    maximum -= len(json.encoder.encode_basestring_ascii(key))
                else:
                    pending.append(key)
                    maximum -= 2  # numeric/null/bool keys become strings
            pending.extend(item.values())
        elif kind is list or kind is tuple:
            if len(item) > 256:
                return False
            maximum -= 2 + len(item)
            pending.extend(item)
        else:
            return False
        if maximum < 0:
            return False
    return True


try:
    from . import _rc6_json_bound as _native_json_bound
except ImportError:
    _native_json_bound = None


def _small_json_tree(value, maximum=65536, nodes=4096):
    """Select the same bounded size predicate; canonical values still encode normally.

    Source-only tools retain the Python implementation. Frozen images must
    prove the compiled backend during exact-image Predeploy validation.
    """
    if (_native_json_bound is None or type(maximum) is not int or type(nodes) is not int
            or maximum != 65536 or nodes != 4096):
        return _small_json_tree_python(value, maximum, nodes)
    return _native_json_bound.is_small(value, FrozenJSONDict, maximum, nodes)


_SHARED_SNAPSHOT_COMPRESSED_LIMIT = 4 * 1024**2
_SHARED_SNAPSHOT_RAW_LIMIT = 32 * 1024**2


def _canonical_fragments(encoder, payload, *, immutable_shared_rows=()):
    """Encode actual values; memoize only explicitly owned immutable row lists.

    The private worker supplies shared preopen lists which cannot change during
    this write. The default generic path has no snapshot memo. No constructor
    encoding hint is used as content authority; the first pass uses the normal
    encoder and every later pass verifies the captured byte digest.
    """
    active = set()
    targets = {}
    for rows in immutable_shared_rows:
        if type(rows) is not list:
            raise ValueError("SHADOW_IMMUTABLE_ROWS_LIST_REQUIRED")
        targets[id(rows)] = rows
    memo = {}
    charged = 0
    capturing = False

    def fragments(value):
        nonlocal charged, capturing
        identity = id(value)
        cached = memo.get(identity)
        if cached is not None:
            original, pieces, expected_bytes, expected_digest = cached
            if original is not value:
                raise ValueError("SHADOW_SNAPSHOT_CACHE_IDENTITY_MISMATCH")
            decoded = 0
            hasher = hashlib.sha256()
            inflater = zlib.decompressobj()
            for piece in pieces:
                pending = piece
                while pending:
                    block = inflater.decompress(pending, 65536)
                    pending = inflater.unconsumed_tail
                    if block:
                        decoded += len(block)
                        if decoded > expected_bytes:
                            raise ValueError("SHADOW_SNAPSHOT_CACHE_SIZE_MISMATCH")
                        hasher.update(block)
                        yield block.decode("ascii")
            while True:
                block = inflater.decompress(b"", 65536)
                if not block:
                    break
                decoded += len(block)
                if decoded > expected_bytes:
                    raise ValueError("SHADOW_SNAPSHOT_CACHE_SIZE_MISMATCH")
                hasher.update(block)
                yield block.decode("ascii")
            if (not inflater.eof or inflater.unused_data or decoded != expected_bytes
                    or hasher.hexdigest() != expected_digest):
                raise ValueError("SHADOW_SNAPSHOT_CACHE_DIGEST_MISMATCH")
            return
        if identity not in targets or capturing:
            yield from fresh(value)
            return
        compressor = zlib.compressobj(level=1)
        pieces, pending = [], []
        pending_size = cost = raw_size = 0
        raw_hash = hashlib.sha256()
        fits = True

        def remember(piece):
            nonlocal cost, fits
            if not piece:
                return
            cost += sys.getsizeof(piece) + 16
            if charged + cost + 512 <= _SHARED_SNAPSHOT_COMPRESSED_LIMIT:
                pieces.append(piece)
            else:
                pieces.clear()
                fits = False

        def flush_pending():
            nonlocal pending_size
            if pending:
                raw = "".join(pending).encode("ascii")
                raw_hash.update(raw)
                remember(compressor.compress(raw))
                pending.clear()
                pending_size = 0

        capturing = True
        try:
            for fragment in fresh(value):
                if fits:
                    raw_size += len(fragment)
                    if raw_size > _SHARED_SNAPSHOT_RAW_LIMIT:
                        pieces.clear()
                        pending.clear()
                        fits = False
                    else:
                        # A scalar string may exceed one encoder chunk. Never
                        # create an unbounded joined capture buffer for it.
                        for offset in range(0, len(fragment), 65536):
                            part = fragment[offset:offset + 65536]
                            if pending and pending_size + len(part) > 65536:
                                flush_pending()
                            if not fits:
                                break
                            pending.append(part)
                            pending_size += len(part)
                yield fragment
            if fits:
                flush_pending()
                if fits:
                    remember(compressor.flush())
        finally:
            capturing = False
        if fits:
            memo[identity] = (value, tuple(pieces), raw_size, raw_hash.hexdigest())
            charged += cost + 512

    def fresh(value):
        container = isinstance(value, (dict, list, tuple))
        if not container or _small_json_tree(value):
            yield encoder.encode(value)
            return
        if id(value) in active:
            raise ValueError("Circular reference detected")
        active.add(id(value))
        try:
            if isinstance(value, dict):
                yield "{"
                for index, key in enumerate(sorted(value)):
                    if index:
                        yield ","
                    key_object = encoder.encode({key: None})
                    yield key_object[1:-len(":null}")]
                    yield ":"
                    yield from fragments(value[key])
                yield "}"
            else:
                yield "["
                for index, item in enumerate(value):
                    if index:
                        yield ","
                    yield from fragments(item)
                yield "]"
        finally:
            active.remove(id(value))
    yield from fragments(payload)


def _canonical_chunks(encoder, payload, block_bytes=65536, *, immutable_shared_rows=()):
    """Bounded byte blocks preserve the exact canonical JSON token stream."""
    # These canonical encoders escape non-ASCII characters, so character and
    # UTF-8 byte lengths agree. Join small tokens in C before encoding them.
    pending, pending_size = [], 0
    fragments = (_canonical_fragments(encoder, payload, immutable_shared_rows=immutable_shared_rows)
                 if immutable_shared_rows else _canonical_fragments(encoder, payload))
    for fragment in fragments:
        size = len(fragment)
        if pending and pending_size + size > block_bytes:
            yield ''.join(pending).encode('ascii')
            pending, pending_size = [], 0
        if size > block_bytes:
            for offset in range(0, size, block_bytes):
                yield fragment[offset:offset + block_bytes].encode('ascii')
        else:
            pending.append(fragment)
            pending_size += size
    if pending:
        yield ''.join(pending).encode('ascii')


class EvidencePayloadLimit(ValueError):
    """Bounded context for a rejected evidence payload; never contains its data."""
    def __init__(self, name, measured_bytes, limit_bytes):
        super().__init__("SHADOW_PAYLOAD_LIMIT")
        self.evidence_file = (name if name in {"latest.json.gz", "checkpoint.json.gz", "status.json"}
                              else "preopen" if name.startswith("preopen-") and name.endswith(".json.gz")
                              else "other")
        self.measured_bytes = int(measured_bytes)
        self.limit_bytes = int(limit_bytes)


class EvidenceFiles:
    def __init__(self, root, *, protected=(), maximum_bytes=128 * 1024**2,
                 payload_limit=64 * 1024**2):
        raw = Path(root).absolute()
        if raw.is_symlink():
            raise ValueError("SHADOW_DIRECTORY_ALIAS_FORBIDDEN")
        self.root = raw.resolve()
        self.protected = {Path(p).resolve() for p in protected if p is not None}
        if any(p == self.root or p.is_relative_to(self.root) for p in self.protected):
            raise ValueError("SHADOW_OUTPUT_MUST_BE_SEPARATE")
        if maximum_bytes <= 0 or payload_limit <= 0:
            raise ValueError("INVALID_EVIDENCE_BUDGET")
        self.maximum_bytes, self.payload_limit = maximum_bytes, payload_limit
        self.lock = None

    def __enter__(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.path("writer.lock")
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        self.lock = os.fdopen(fd, "a+")
        try:
            if os.fstat(fd).st_nlink != 1:
                raise ValueError("SHADOW_FILE_ALIAS_FORBIDDEN")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.lock.close()
            self.lock = None
            raise
        return self

    def __exit__(self, *args):
        if self.lock is not None:
            self.lock.close()
            self.lock = None

    def path(self, name):
        if Path(name).name != name:
            raise ValueError("SHADOW_BASENAME_REQUIRED")
        target = self.root / name
        if target.is_symlink() or (target.exists() and target.stat().st_nlink != 1):
            raise ValueError("SHADOW_FILE_ALIAS_FORBIDDEN")
        if target.resolve() in self.protected:
            raise ValueError("SHADOW_OUTPUT_MUST_BE_SEPARATE")
        return target

    def read(self, name, *, compact_preopen=False):
        path = self.path(name)
        if not path.exists():
            return None
        if path.stat().st_size > self.payload_limit:
            raise EvidencePayloadLimit(name, path.stat().st_size, self.payload_limit)
        opener = gzip.open if name.endswith(".gz") else open
        with opener(path, "rb") as stream:
            raw = stream.read(self.payload_limit + 1)
        if len(raw) > self.payload_limit:
            raise EvidencePayloadLimit(name, len(raw), self.payload_limit)
        if compact_preopen and not re.fullmatch(r"preopen-\d{4}-\d{2}-\d{2}\.json\.gz", name):
            raise ValueError("SHADOW_COMPACT_PREOPEN_ROLE_REQUIRED")
        # json.loads(bytes) retains the byte document while constructing the
        # decoded object graph. Release it before construction. Preserve the
        # stdlib's UTF-8/16/32 and surrogate policy for legacy envelopes.
        text = raw.decode(json.detect_encoding(raw), "surrogatepass")
        del raw
        envelope = json.loads(text, object_hook=_preopen_scalar_decoder() if compact_preopen else None)
        del text
        calculated = streamed_digest(envelope.get("payload"))
        if envelope.get("digest") != calculated:
            raise ValueError("SHADOW_EVIDENCE_DIGEST_MISMATCH")
        return envelope["payload"]

    def write(self, name, payload, *, immutable=False):
        if self.lock is None:
            raise ValueError("SHADOW_WRITER_LOCK_REQUIRED")
        target = self.path(name)
        if immutable and target.exists():
            existing = self.read(name)
            if digest(existing) != digest(payload):
                raise ValueError("SHADOW_IMMUTABLE_FREEZE_CONFLICT")
            return
        # One bounded canonical pass hashes and compresses the payload. The
        # digest follows it in the envelope; readers already validate by keys.
        # A zlib call per tiny JSON token stalled full-catalog generations.
        encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"),
                                   default=str, allow_nan=False)
        hasher = hashlib.sha256()
        prefix = b'{"payload":'
        overhead = len(prefix) + len(b',"digest":"') + 64 + len(b'"}')

        def emit(sink):
            payload_bytes = 0
            sink.write(prefix)
            for chunk in _canonical_chunks(encoder, payload):
                payload_bytes += len(chunk)
                if payload_bytes + overhead > self.payload_limit:
                    raise EvidencePayloadLimit(name, payload_bytes + overhead, self.payload_limit)
                hasher.update(chunk)
                sink.write(chunk)
            sink.write(b',"digest":"' + hasher.hexdigest().encode() + b'"}')

        files = list(self.root.iterdir())
        # The fsynced staging file temporarily coexists with the prior target.
        # Both file and byte quotas bound that peak, not just the final rename.
        if len(files) + 1 > 512:
            raise ValueError("SHADOW_EVIDENCE_CAPACITY_REACHED")
        size = sum(p.lstat().st_size for p in files)
        with tempfile.NamedTemporaryFile(dir=self.root, prefix="." + name + ".",
                suffix=".tmp", delete=False) as stream:
            tmp = Path(stream.name)
            try:
                if name.endswith(".gz"):
                    with gzip.GzipFile(fileobj=stream, filename="", mode="wb", compresslevel=1,
                                       mtime=0) as zipped:
                        emit(zipped)
                else:
                    emit(stream)
                stream.flush()
                os.fsync(stream.fileno())
                if size + stream.tell() > self.maximum_bytes:
                    raise ValueError("SHADOW_EVIDENCE_CAPACITY_REACHED")
                # No overwrite of existing sources via a deterministic .tmp alias.
                tmp.replace(target)
                directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                tmp.unlink(missing_ok=True)

    def write_streamed_report(self, payload, *, raw_limit=512 * 1024**2, immutable_shared_rows=()):
        """Store canonical full audit separately from the bounded hot-path view."""
        if self.lock is None:
            raise ValueError("SHADOW_WRITER_LOCK_REQUIRED")
        encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"),
                                   default=str, allow_nan=False)
        hasher = hashlib.sha256()
        raw_bytes = 0
        profile = {name: 0.0 for name in ('encode_seconds', 'encode_cpu_seconds',
            'compress_hash_seconds', 'compress_hash_cpu_seconds', 'fsync_seconds', 'fsync_cpu_seconds')}
        def charge(name, wall, cpu):
            profile[name + '_seconds'] += time.monotonic() - wall
            profile[name + '_cpu_seconds'] += time.process_time() - cpu
        with tempfile.NamedTemporaryFile(dir=self.root, prefix=".computed-report.",
                suffix=".tmp", delete=False) as stream:
            tmp = Path(stream.name)
            try:
                with gzip.GzipFile(fileobj=stream, mode="wb", compresslevel=1, mtime=0) as zipped:
                    chunks = (_canonical_chunks(encoder, payload, immutable_shared_rows=immutable_shared_rows)
                              if immutable_shared_rows else _canonical_chunks(encoder, payload))
                    chunks = iter(chunks)
                    while True:
                        wall, cpu = time.monotonic(), time.process_time()
                        try:
                            chunk = next(chunks)
                        except StopIteration:
                            charge('encode', wall, cpu)
                            break
                        charge('encode', wall, cpu)
                        raw_bytes += len(chunk)
                        if raw_bytes > raw_limit:
                            raise EvidencePayloadLimit("computed-report", raw_bytes, raw_limit)
                        wall, cpu = time.monotonic(), time.process_time()
                        hasher.update(chunk)
                        zipped.write(chunk)
                        charge('compress_hash', wall, cpu)
                wall, cpu = time.monotonic(), time.process_time()
                stream.flush()
                os.fsync(stream.fileno())
                charge('fsync', wall, cpu)
                name = "computed-" + hasher.hexdigest() + ".json.gz"
                target = self.path(name)
                files = list(self.root.iterdir())
                if len(files) > 512 or sum(p.lstat().st_size for p in files) > self.maximum_bytes:
                    raise ValueError("SHADOW_EVIDENCE_CAPACITY_REACHED")
                if not target.exists():
                    tmp.replace(target)
                    directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                self.report_write_profile = {key: round(value, 6) for key, value in profile.items()}
                self.report_write_profile.update(raw_bytes=raw_bytes, compressed_bytes=target.stat().st_size)
                return {"file": name, "sha256": hasher.hexdigest(),
                        "raw_bytes": raw_bytes, "compressed_bytes": target.stat().st_size}
            finally:
                tmp.unlink(missing_ok=True)

    def prune_computed_reports(self, *, keep):
        """After the new view and status commit, remove superseded private audits."""
        if self.lock is None or not re.fullmatch(r"computed-[a-f0-9]{64}\.json\.gz", keep):
            raise ValueError("SHADOW_AUDIT_PRUNE_INVALID")
        for target in self.root.glob("computed-*.json.gz"):
            if target.name != keep and re.fullmatch(r"computed-[a-f0-9]{64}\.json\.gz", target.name):
                self.path(target.name).unlink()

    def publish_generation(self, report, status):
        """Publish the small dashboard cut only after all worker writes commit.

        The causal checkpoint remains private to the worker. Readers need the
        exact selection report and status, not a 64 MiB checkpoint per request.
        A single atomic pointer commits both immutable members together.
        """
        if (report.get("as_of") != status.get("as_of") or
                digest(report) != status.get("report_digest")):
            raise ValueError("GENERATION_REPORT_STATUS_MISMATCH")
        watermark = {"as_of": report["as_of"]}
        configuration = status["configuration_fingerprint"]
        ident = digest({"report": report, "status": status})
        from datetime import datetime
        sequence = int(datetime.fromisoformat(report["as_of"].replace("Z", "+00:00")).timestamp()*1000000)
        members = {role: {**member, "generation_id": ident,
                   "source_watermark": watermark, "configuration_fingerprint": configuration}
                   for role, member in (("report", report), ("status", status))}
        manifest = {"schema": "rc6.dashboard-generation.v1", "generation_id": ident,
                    "sequence": sequence, "source_watermark": watermark,
                    "configuration_fingerprint": configuration,
                    "files": {role: {"payload_digest": digest(member)}
                              for role, member in members.items()}}
        name = "cut-" + ident + ".json.gz"
        cut = {"manifest": manifest, **members}
        encoded_size = sum(len(fragment.encode()) for fragment in json.JSONEncoder(
            sort_keys=True,separators=(",", ":"),default=str,allow_nan=False).iterencode(cut))
        if encoded_size > 4 * 1024**2 - 1024:
            raise ValueError("SHADOW_DASHBOARD_CUT_LIMIT")
        old = self.read("CURRENT.json")
        if old and sequence <= old['sequence'] and old['generation_id'] != ident:
            raise ValueError("GENERATION_CLOCK_REGRESSION")
        self.write(name, cut, immutable=True)
        self.write("CURRENT.json", {"generation_id": ident, "sequence": sequence,
                   "manifest_sha256": digest(manifest), "file": name})
        # Keep the previous generation for a concurrent reader. A reader of a
        # much older pointer fails closed if it loses a race with bounded GC.
        cuts = sorted(self.root.glob("cut-*.json.gz"), key=lambda p: p.stat().st_mtime_ns, reverse=True)
        for old in cuts[2:]:
            if re.fullmatch(r"cut-[a-f0-9]{64}\.json\.gz", old.name):
                self.path(old.name).unlink()
        return {"generation_id": ident, "sequence": sequence,
                "manifest_sha256": digest(manifest)}


def read_committed_generation(root, *, payload_limit=4 * 1024**2, require_current_status=True):
    """Read immutable bytes. Historical consumers may explicitly waive live health.

    The default always checks current status; the historical option never
    attests runtime health and retains manifest, identity and safety checks.
    """
    if type(require_current_status) is not bool:
        raise ValueError("GENERATION_READER_MODE_INVALID")
    files = EvidenceFiles(root, payload_limit=payload_limit)
    pointer = files.read("CURRENT.json")
    if not isinstance(pointer, dict):
        raise ValueError("COMMITTED_GENERATION_MISSING")
    name = pointer.get("file", "")
    if not isinstance(name, str) or not re.fullmatch(r"cut-[a-f0-9]{64}\.json\.gz", name):
        raise ValueError("GENERATION_PATH_INVALID")
    cut = files.read(name)
    if not isinstance(cut, dict):
        raise ValueError("GENERATION_CUT_MISSING")
    manifest = cut.get("manifest", {})
    if (digest(manifest) != pointer.get("manifest_sha256") or
            manifest.get("generation_id") != pointer.get("generation_id") or
            manifest.get("sequence") != pointer.get("sequence")):
        raise ValueError("GENERATION_ENVELOPE_MISMATCH")
    for role in ("report", "status"):
        member = cut[role]
        if (digest(member) != manifest["files"][role]["payload_digest"] or
                member.get("generation_id") != pointer["generation_id"] or
                member.get("source_watermark") != manifest["source_watermark"] or
                member.get("configuration_fingerprint") != manifest["configuration_fingerprint"] or
                member.get("as_of") != manifest["source_watermark"]["as_of"]):
            raise ValueError("GENERATION_MEMBER_MISMATCH")
    # A new failure revokes a previously good cut, even if CURRENT was not
    # advanced. An incomplete publication also cannot resurrect old health.
    status = cut["status"]
    if require_current_status:
        live = files.read("status.json")
        if (not isinstance(live, dict) or live.get("as_of") != status.get("as_of") or
                live.get("status") != status.get("status") or
                live.get("report_digest") != status.get("report_digest")):
            raise ValueError("GENERATION_REVOKED_OR_PUBLICATION_IN_PROGRESS")
    for member in (cut["report"], status):
        if (type(member.get("real_orders_sent")) is not int or member["real_orders_sent"] != 0 or
                member.get("real_routes") != "NOT_CALLED"):
            raise ValueError("GENERATION_SAFETY_UNVERIFIED")
    result = {"pointer": pointer, "live_status_required":require_current_status, **cut}
    # Measurement is outside the immutable generation hash to avoid recursion.
    # A missing/racing receipt is visible to strict deployment consumers;
    # ordinary report readers still validate the complete immutable cut above.
    if (status.get('generation_timing') or {}).get('tick_measurement_boundary') == 'BEFORE_STATUS_AND_CURRENT':
        measured = files.read('publication.json')
        if (isinstance(measured,dict) and measured.get('generation_id')==pointer['generation_id']
                and measured.get('manifest_sha256')==pointer['manifest_sha256']
                and measured.get('sequence')==pointer['sequence']
                and measured.get('source_as_of')==status['as_of']):
            result['publication'] = measured
    return result
