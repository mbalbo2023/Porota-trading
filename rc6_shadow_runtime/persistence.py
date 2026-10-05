"""Durable SHADOW generations; CURRENT publication is sealed by lineage custody.

Report/checkpoint/status are immutable, hash-linked members of one generation.
Standalone preopen freezes and failure diagnostics have separate explicit roles.
No reader reconstructs a snapshot from file mtimes or independently written JSON.
"""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import errno
import fcntl
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid
import zlib

from rc6_dynamic_universe.common import digest, stamp
from rc6_dynamic_universe.sources import audit_sources, source_errors_are_sanitized
from .serialization import encode_storage, decode_storage, verify_storage_wire, canonical_metrics, PreparedStorage, SCHEMA as STORAGE_SCHEMA

GENERATION_SCHEMA = "rc6.shadow-evidence-generation.v2"
LEGACY_GENERATION_SCHEMA = "rc6.shadow-evidence-generation.v1"
EXPORT_SCHEMA = "rc6.shadow-committed-cut.v2"
LINEAGE_SCHEMA = "rc6.shadow-lineage-authority.v2"
DEFAULT_MAXIMUM_FILES = 8192
EXPANDED_PAYLOAD_LIMIT = 512 * 1024**2
SAFETY = {"mode": "SHADOW", "real_orders_sent": 0, "real_routes": "NOT_CALLED",
          "provider_requests": 0, "source_database_effect": "READ_ONLY",
          "factual_execution": "NOT_CALLED", "ppi_watch": "UNTOUCHED"}
ROLES = {"report": "report.json.gz", "checkpoint": "checkpoint.json.gz", "status": "status.json"}
GENERATION_ROLES = {**ROLES, "projection": "projection.sqlite"}
LOGICAL_ROLES = {"latest.json.gz": "report", "checkpoint.json.gz": "checkpoint", "status.json": "status"}
ID_PATTERN = re.compile(r"[0-9a-f]{32}\Z")


def shadow_evidence_root(database, environ=None):
    """One explicit root for worker, dashboard and operational health readers."""
    from cg_paper_workspace import artifact_root
    env = os.environ if environ is None else environ
    configured = [Path(env[key].strip()).absolute() for key in
        ("POROTA_DYNAMIC_SHADOW_ROOT", "POROTA_SHADOW_RUNTIME_ROOT") if env.get(key, "").strip()]
    if any(path.is_symlink() for root in configured for path in (root, *root.parents)):
        raise ValueError("SHADOW_DIRECTORY_ALIAS_FORBIDDEN")
    if len(configured) == 2 and configured[0].resolve() != configured[1].resolve():
        raise ValueError("SHADOW_ROOT_CONFIGURATION_CONFLICT")
    root = configured[0] if configured else artifact_root(database) / "dynamic-shadow"
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError("SHADOW_DIRECTORY_ALIAS_FORBIDDEN")
    database_path = Path(database).resolve()
    if root.resolve() == database_path or database_path.is_relative_to(root.resolve()):
        raise ValueError("SHADOW_OUTPUT_MUST_BE_SEPARATE")
    return root


def shadow_archive_root(database, environ=None):
    """Canonical private sibling archive, separate from every input/output."""
    from cg_paper_workspace import artifact_root
    env = os.environ if environ is None else environ
    configured = env.get("POROTA_DYNAMIC_SHADOW_ARCHIVE_ROOT", "").strip()
    root = Path(configured).absolute() if configured else artifact_root(database) / "dynamic-shadow-archive"
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError("SHADOW_DIRECTORY_ALIAS_FORBIDDEN")
    resolved, database_path = root.resolve(), Path(database).resolve()
    evidence = shadow_evidence_root(database, env).resolve()
    if (resolved == database_path or database_path.is_relative_to(resolved)
            or resolved == evidence or resolved.is_relative_to(evidence) or evidence.is_relative_to(resolved)):
        raise ValueError("SHADOW_ARCHIVE_MUST_BE_SEPARATE")
    if root.exists() and not root.is_dir():
        raise ValueError("SHADOW_ARCHIVE_DIRECTORY_REQUIRED")
    return root


def shadow_archive_maximum_bytes(environ=None):
    """Parse the configured archive cap without increasing the 512-MiB bound."""
    from .archive_namespace import DEFAULT_MAXIMUM_BYTES
    env = os.environ if environ is None else environ
    value = env.get("POROTA_DYNAMIC_SHADOW_ARCHIVE_MAX_BYTES", str(DEFAULT_MAXIMUM_BYTES)).strip()
    if not re.fullmatch(r"[1-9][0-9]{0,9}", value):
        raise ValueError("SHADOW_ARCHIVE_BYTE_POLICY_INVALID")
    maximum = int(value)
    if maximum > DEFAULT_MAXIMUM_BYTES:
        raise ValueError("SHADOW_ARCHIVE_BYTE_POLICY_INVALID")
    return maximum


def _semantic_sources(report, *, legacy=False):
    reports, audit = report.get("source_reports", []), report.get("source_audit", {})
    if not isinstance(reports, list) or not isinstance(audit, dict):
        raise ValueError("SHADOW_SOURCE_AUDIT_INVALID")
    if reports and audit.get("source_reports_digest") != digest(reports):
        raise ValueError("SHADOW_SOURCE_AUDIT_DIGEST_MISMATCH")
    if not legacy and not source_errors_are_sanitized(reports):
        raise ValueError("SHADOW_SOURCE_ERROR_TAXONOMY_MISMATCH")
    expected = audit_sources(reports=reports, as_of=report["as_of"])
    if legacy and not reports and not audit:
        return expected
    if digest(audit) != digest(expected):
        raise ValueError("SHADOW_SOURCE_AUDIT_SEMANTIC_MISMATCH")
    return expected


def _semantic_safety(values, *, fill=False):
    """Validate supplied declarations before filling fixed SHADOW role fields."""
    for value in values.values():
        for key, expected in SAFETY.items():
            if key in value and (value[key] != expected or type(value[key]) is not type(expected)):
                raise ValueError("SHADOW_GENERATION_SAFETY_MISMATCH")
        if value.get("real_order_routes", "NOT_CALLED") != "NOT_CALLED":
            raise ValueError("SHADOW_GENERATION_SAFETY_MISMATCH")
        if value.get("real_money_authorized", False) is not False or value.get("live_decision_authority", False) is not False:
            raise ValueError("SHADOW_GENERATION_SAFETY_MISMATCH")
        if "provider_additional_budget" in value:
            budget = value["provider_additional_budget"]
            if (not isinstance(budget, dict) or set(budget) != {"current", "book", "intraday"}
                    or any(type(amount) is not int or amount != 0 for amount in budget.values())):
                raise ValueError("SHADOW_GENERATION_SAFETY_MISMATCH")
        if "safety" in value and digest(value["safety"]) != digest(SAFETY):
            raise ValueError("SHADOW_GENERATION_SAFETY_MISMATCH")
        if fill:
            value.update(deepcopy(SAFETY))
            value["safety"] = deepcopy(SAFETY)
        elif digest(value.get("safety")) != digest(SAFETY) or any(value.get(key) != expected for key, expected in SAFETY.items()):
            raise ValueError("SHADOW_GENERATION_SAFETY_MISMATCH")
    declared_limits = [value["production_limits_modified"] for value in values.values() if "production_limits_modified" in value]
    if any(type(value) is not bool for value in declared_limits) or len({digest(value) for value in declared_limits}) > 1:
        raise ValueError("SHADOW_GENERATION_SAFETY_MISMATCH")


def failure_reason(error):
    """Only stable reason codes, never arbitrary exception/provider messages."""
    code = str(getattr(error, "reason", str(error)))
    prefixes = ("SHADOW_", "RETENTION_", "FUNNEL_", "ENTRY_", "LAB_", "PREOPEN_",
                "AUDITED_", "SOURCE_", "PAPER_", "CAPACITY_", "PPI_", "FAMILY_")
    if re.fullmatch(r"[A-Z][A-Z0-9_]{2,127}", code) and code.startswith(prefixes):
        return code
    if isinstance(error, OSError):
        return {errno.ENOSPC: "SHADOW_EVIDENCE_DISK_FULL", errno.EACCES: "SHADOW_EVIDENCE_PERMISSION_DENIED",
                errno.EROFS: "SHADOW_EVIDENCE_PERMISSION_DENIED", errno.ENOMEM: "SHADOW_RESOURCE_PRESSURE",
                errno.EIO: "SHADOW_EVIDENCE_IO_FAILURE"}.get(error.errno, "SHADOW_EVIDENCE_IO_FAILURE")
    return type(error).__name__


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False).encode()


def _json(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError("SHADOW_DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    def constant(_):
        raise ValueError("SHADOW_NONFINITE_JSON")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


class EvidenceFiles:
    def __init__(self, root, *, protected=(), maximum_bytes=128 * 1024**2,
                 maximum_files=DEFAULT_MAXIMUM_FILES, payload_limit=64 * 1024**2,
                 fault_inject=None, archive_root=None, archive_maximum_bytes=512 * 1024**2):
        raw = Path(root).absolute()
        if any(p.is_symlink() for p in (raw, *raw.parents)):
            raise ValueError("SHADOW_DIRECTORY_ALIAS_FORBIDDEN")
        self.root = raw.resolve()
        self.protected = {Path(p).resolve() for p in protected if p is not None}
        if any(p == self.root or p.is_relative_to(self.root) for p in self.protected):
            raise ValueError("SHADOW_OUTPUT_MUST_BE_SEPARATE")
        if maximum_bytes <= 0 or payload_limit <= 0:
            raise ValueError("INVALID_EVIDENCE_BUDGET")
        self.maximum_bytes, self.payload_limit = maximum_bytes, payload_limit
        if type(maximum_files) is not int or maximum_files <= 0:
            raise ValueError("INVALID_EVIDENCE_BUDGET")
        self.maximum_files = maximum_files
        self.archive_root = Path(archive_root).absolute() if archive_root is not None else None
        if self.archive_root is not None:
            if any(path.is_symlink() for path in (self.archive_root, *self.archive_root.parents)):
                raise ValueError("SHADOW_DIRECTORY_ALIAS_FORBIDDEN")
            archive_resolved = self.archive_root.resolve()
            if any(p == archive_resolved or p.is_relative_to(archive_resolved) for p in self.protected):
                raise ValueError("SHADOW_OUTPUT_MUST_BE_SEPARATE")
        self.archive_maximum_bytes = archive_maximum_bytes
        self.authority_root = self.root.parent / (self.root.name + ".authority")
        if any(p == self.authority_root or p.is_relative_to(self.authority_root) for p in self.protected):
            raise ValueError("SHADOW_OUTPUT_MUST_BE_SEPARATE")
        if any(path.is_symlink() for path in (self.authority_root, *self.authority_root.parents)):
            raise ValueError("SHADOW_DIRECTORY_ALIAS_FORBIDDEN")
        self.lock = None
        self._wire_receipts = {}
        self.fault_inject = fault_inject

    def __enter__(self):
        if self.lock is not None:
            raise ValueError("SHADOW_WRITER_LOCK_ALREADY_HELD")
        missing = []
        for path in (self.root, *self.root.parents):
            if path.exists():
                break
            missing.append(path)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        for path in reversed(missing):
            self._sync_directory(path.parent)
        path = self.path("writer.lock")
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        self.lock = os.fdopen(fd, "a+")
        try:
            if os.fstat(fd).st_nlink != 1 or not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("SHADOW_FILE_ALIAS_FORBIDDEN")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._recover_publication()
            self._wire_receipts.clear()
        except BaseException:
            self.lock.close()
            self.lock = None
            raise
        return self

    def __exit__(self, *args):
        self._wire_receipts.clear()
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

    @contextmanager
    def _reader(self):
        # Readers never mkdir/create files and never wait behind expensive
        # SHADOW serialization. Factual callers may immediately fall back.
        if self.lock is not None:
            yield
            return
        fd = os.open(self.path("writer.lock"), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_NOATIME", 0))
        try:
            if os.fstat(fd).st_nlink != 1 or not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("SHADOW_FILE_ALIAS_FORBIDDEN")
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)

    def _bytes(self, path, *, limit=None):
        limit = self.payload_limit if limit is None else limit
        # Open members relative to directory descriptors. O_NOFOLLOW on the
        # leaf alone would still follow a swapped generation-directory alias.
        parts = Path(path).relative_to(self.root).parts
        directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for name in parts[:-1]:
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NOATIME", 0), dir_fd=directory)
        finally:
            os.close(directory)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("SHADOW_FILE_ALIAS_FORBIDDEN")
            if info.st_size > limit:
                raise ValueError("SHADOW_PAYLOAD_LIMIT")
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("SHADOW_PAYLOAD_LIMIT")
        return raw

    def _payload(self, name, raw, *, details=False):
        if name.endswith(".gz"):
            try:
                with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                    raw = stream.read(self.payload_limit + 1)
            except (OSError, EOFError, zlib.error) as error:
                raise ValueError("SHADOW_GENERATION_GZIP_INVALID") from error
            if len(raw) > self.payload_limit:
                raise ValueError("SHADOW_PAYLOAD_LIMIT")
        envelope = _json(raw)
        if not isinstance(envelope, dict):
            raise ValueError("SHADOW_EVIDENCE_DIGEST_MISMATCH")
        payload = decode_storage(envelope.get("payload"), durable_limit=self.payload_limit,
                                 expansion_limit=EXPANDED_PAYLOAD_LIMIT, share_subtrees=name == ROLES["report"])
        if payload is not envelope["payload"]:
            actual, logical_size = envelope["payload"]["logical_sha256"], envelope["payload"]["logical_bytes"]
        else:
            actual, logical_size = canonical_metrics(payload, ensure_ascii=True, limit=EXPANDED_PAYLOAD_LIMIT)
        if envelope.get("digest") != actual:
            raise ValueError("SHADOW_EVIDENCE_DIGEST_MISMATCH")
        return (payload, {"payload_digest": actual, "logical_bytes": logical_size,
            "storage_schema": STORAGE_SCHEMA if payload is not envelope["payload"] else "PLAIN_JSON"}) if details else payload

    def _independent(self, name):
        path = self.path(name)
        try:
            raw = self._bytes(path)
        except FileNotFoundError:
            return None
        return self._payload(name, raw)

    def _pointer(self):
        try:
            pointer = _json(self._bytes(self.path("CURRENT.json"), limit=64 * 1024))
        except FileNotFoundError:
            return None
        if (not isinstance(pointer, dict) or pointer.get("schema") not in {GENERATION_SCHEMA, LEGACY_GENERATION_SCHEMA}
                or not ID_PATTERN.fullmatch(str(pointer.get("generation_id", "")))
                or type(pointer.get("sequence")) is not int or pointer["sequence"] <= 0
                or not re.fullmatch(r"[0-9a-f]{64}", str(pointer.get("manifest_sha256", "")))
                or pointer.get("digest") != digest({k: v for k, v in pointer.items() if k != "digest"})):
            raise ValueError("SHADOW_CURRENT_INVALID")
        return pointer

    def _authority(self):
        """Bounded high-water outside the root restored by CURRENT snapshots.

        Custody is local filesystem ownership, not WORM or external writer
        authentication. An actor able to rewrite both roots is outside this
        guarantee; independent authentication requires an external anchor.
        """
        reader = EvidenceFiles(self.authority_root, payload_limit=64 * 1024)
        try:
            value = _json(reader._bytes(self.authority_root / "HEAD.json", limit=64 * 1024))
        except FileNotFoundError:
            return None
        if (not isinstance(value, dict) or value.get("schema") != LINEAGE_SCHEMA
                or type(value.get("allocated_sequence")) is not int or value["allocated_sequence"] < 0
                or value.get("digest") != digest({key: item for key, item in value.items() if key != "digest"})):
            raise ValueError("SHADOW_LINEAGE_AUTHORITY_INVALID")
        for key in ("committed", "prepared"):
            head = value.get(key)
            if head is not None and (not isinstance(head, dict) or type(head.get("sequence")) is not int
                    or not 0 < head["sequence"] <= value["allocated_sequence"]
                    or head.get("schema") not in {GENERATION_SCHEMA, LEGACY_GENERATION_SCHEMA}
                    or not ID_PATTERN.fullmatch(str(head.get("generation_id", "")))
                    or not re.fullmatch(r"[0-9a-f]{64}", str(head.get("manifest_sha256", "")))
                    or head.get("digest") != digest({k: v for k, v in head.items() if k != "digest"})):
                raise ValueError("SHADOW_LINEAGE_AUTHORITY_INVALID")
        phase = value.get("phase", "PREPARED" if value.get("prepared") else "COMMITTED")
        if phase not in {"PREPARED", "PUBLISHING", "COMMITTED"} or (phase == "COMMITTED") != (value.get("prepared") is None):
            raise ValueError("SHADOW_LINEAGE_AUTHORITY_INVALID")
        if value.get("prepared") is not None and (value["prepared"]["sequence"] != value["allocated_sequence"]
                or value["prepared"]["sequence"] <= (value.get("committed") or {}).get("sequence", 0)):
            raise ValueError("SHADOW_LINEAGE_AUTHORITY_INVALID")
        value["phase"] = phase
        return value

    def _write_authority(self, value):
        self.authority_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Created only by the exclusive evidence writer; readers never repair.
        if any(path.is_symlink() for path in (self.authority_root, *self.authority_root.parents)):
            raise ValueError("SHADOW_DIRECTORY_ALIAS_FORBIDDEN")
        for path in self.authority_root.iterdir():
            if re.fullmatch(r"\.HEAD-[0-9a-f]{32}\.tmp", path.name):
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError("SHADOW_FILE_ALIAS_FORBIDDEN")
                path.unlink()
        value = {**value, "schema": LINEAGE_SCHEMA,
                 "custody": "LOCAL_DURABLE_CUSTODY_NOT_EXTERNAL_AUTHENTICATION"}
        value.setdefault("phase", "PREPARED" if value.get("prepared") else "COMMITTED")
        value["digest"] = digest({k: v for k, v in value.items() if k != "digest"})
        target = self.authority_root / "HEAD.json"
        if target.is_symlink() or (target.exists() and target.stat().st_nlink != 1):
            raise ValueError("SHADOW_FILE_ALIAS_FORBIDDEN")
        temporary = self.authority_root / (".HEAD-" + uuid.uuid4().hex + ".tmp")
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(_encode(value)); stream.flush(); os.fsync(stream.fileno())
            temporary.replace(target)
            self._sync_directory(self.authority_root)
            self._sync_directory(self.authority_root.parent)
        finally:
            temporary.unlink(missing_ok=True)

    def _validate_authority(self, pointer, *, allow_legacy=False, recovering=False):
        authority = self._authority()
        if authority is None:
            if pointer is None or (allow_legacy and pointer["schema"] == LEGACY_GENERATION_SCHEMA):
                return None
            raise ValueError("SHADOW_LINEAGE_ANCHOR_MISSING")
        if authority["phase"] == "PUBLISHING":
            if recovering and pointer == authority.get("prepared"):
                return authority
            raise ValueError("SHADOW_PUBLICATION_RECOVERY_REQUIRED")
        if pointer is None and authority.get("committed") is None:
            return authority
        if pointer is not None and pointer == authority.get("committed"):
            return authority
        committed = authority.get("committed") or {}
        if pointer is None or pointer.get("sequence", 0) < committed.get("sequence", 0):
            raise ValueError("SHADOW_CURRENT_ROLLBACK")
        raise ValueError("SHADOW_LINEAGE_FORK_OR_REHASH")

    def _recover_publication(self):
        """Only the writer seals an interrupted publication, always forward.

        A reader cannot publish/repair or accept an unsealed CURRENT. Before
        changing its mirror, recovery validates the exact prepared immutable
        cut against the separate authority and every semantic/member guard.
        """
        authority = self._authority()
        if not authority or authority["phase"] != "PUBLISHING":
            return
        pointer = self._pointer()
        if pointer not in (authority.get("committed"), authority.get("prepared")):
            raise ValueError("SHADOW_LINEAGE_FORK_OR_REHASH")
        prepared = authority["prepared"]
        self.read_generation(allow_degraded=True, _pointer_override=prepared, _recovering=True)
        previous_temp = self.root / (".CURRENT." + prepared["generation_id"] + ".tmp")
        if previous_temp.exists() or previous_temp.is_symlink():
            info = previous_temp.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("SHADOW_FILE_ALIAS_FORBIDDEN")
            previous_temp.unlink(); self._sync_directory(self.root)
        temporary = self.root / (".CURRENT." + uuid.uuid4().hex + ".tmp")
        try:
            self._durable_member(temporary, _encode(prepared))
            temporary.replace(self.path("CURRENT.json")); self._sync_directory(self.root)
            authority.update(committed=prepared, prepared=None, phase="COMMITTED")
            self._write_authority(authority)
        finally:
            temporary.unlink(missing_ok=True)

    def read_generation(self, *, allow_degraded=False, allow_legacy=False, roles=None,
                        _pointer_override=None, _recovering=False):
        """Read one committed cut and verify every member, even for one role.

        A coherent older CURRENT remains a coherent older cut, never a new cut.
        Consumers enforce source/as_of freshness; mtime is never authoritative.
        """
        requested = tuple(GENERATION_ROLES) if roles is None else tuple(roles)
        if len(set(requested)) != len(requested) or any(role not in GENERATION_ROLES for role in requested):
            raise ValueError("SHADOW_EXPORT_ROLE_INVALID")
        with self._reader():
            pointer = _pointer_override if _pointer_override is not None else self._pointer()
            if pointer is None:
                self._validate_authority(pointer, allow_legacy=allow_legacy)
                return None
            legacy = pointer["schema"] == LEGACY_GENERATION_SCHEMA
            if legacy and not allow_legacy:
                raise ValueError("SHADOW_GENERATION_LEGACY_REQUIRES_FORWARD_BOOTSTRAP")
            generation = self.root / ("gen-" + pointer["generation_id"])
            if generation.is_symlink() or not generation.is_dir():
                raise ValueError("SHADOW_GENERATION_DIRECTORY_INVALID")
            raw = self._bytes(generation / "manifest.json", limit=64 * 1024)
            if _sha(raw) != pointer.get("manifest_sha256"):
                raise ValueError("SHADOW_GENERATION_MANIFEST_HASH_MISMATCH")
            manifest = _json(raw)
            if (not isinstance(manifest, dict) or manifest.get("schema") != pointer["schema"]
                    or manifest.get("generation_id") != pointer["generation_id"]
                    or type(manifest.get("sequence")) is not int or manifest.get("sequence") != pointer["sequence"]
                    or not isinstance(manifest.get("source_watermark"), dict)
                    or not isinstance(manifest.get("configuration_fingerprint"), str)
                    or not manifest["configuration_fingerprint"]
                    or not isinstance(manifest.get("files"), dict)
                    or set(manifest.get("files", {})) not in (set(ROLES), set(GENERATION_ROLES))):
                raise ValueError("SHADOW_GENERATION_MANIFEST_MISMATCH")
            payloads, headers, verified, cross_hashes, declared_cross, declared_limits = {}, {}, {}, {}, {}, []
            status_digests = {}
            expected_projection = None
            for role, name in ROLES.items():
                record = manifest["files"][role]
                if not isinstance(record, dict) or record.get("name") != name:
                    raise ValueError("SHADOW_GENERATION_PATH_MISMATCH")
                member = self._bytes(generation / name)
                if _sha(member) != record.get("sha256"):
                    raise ValueError("SHADOW_GENERATION_FILE_HASH_MISMATCH")
                value, proof = self._payload(name, member, details=True)
                if (not isinstance(value, dict) or proof["payload_digest"] != record.get("payload_digest")
                        or value.get("generation_id") != pointer["generation_id"]
                        or value.get("source_watermark") != manifest.get("source_watermark")
                        or value.get("configuration_fingerprint") != manifest.get("configuration_fingerprint")):
                    raise ValueError("SHADOW_GENERATION_MEMBER_MISMATCH")
                if not legacy and (value.get("generation_schema") != GENERATION_SCHEMA
                        or type(value.get("sequence")) is not int or value.get("sequence") != pointer["sequence"]):
                    raise ValueError("SHADOW_GENERATION_MEMBER_MISMATCH")
                if role in ("report", "checkpoint"):
                    cross_hashes[role] = canonical_metrics({key: item for key, item in value.items()
                        if key != "cross_payload_hashes"}, ensure_ascii=True, limit=EXPANDED_PAYLOAD_LIMIT)[0]
                declared_cross[role] = value.get("cross_payload_hashes")
                _semantic_safety({role: value}, fill=legacy)
                if "production_limits_modified" in value:
                    declared_limits.append(value["production_limits_modified"])
                if role == "report":
                    _semantic_sources(value, legacy=legacy)
                    if (manifest.get("source_audit_digest") != digest(value.get("source_audit", {}))
                            or manifest.get("source_reports_digest") != digest(value.get("source_reports", []))):
                        raise ValueError("SHADOW_GENERATION_LOGICAL_MISMATCH")
                    if "projection" in manifest["files"]:
                        from .projection import build_projection, LEGACY_ENCODING, LEGACY_ROW_CODEC
                        _, expected_header, expected_projection = build_projection(value,
                            {key: manifest["files"][key]["payload_digest"] for key in ROLES},
                            logical_encoding=manifest["files"]["projection"].get("logical_encoding", LEGACY_ENCODING),
                            row_codec=manifest["files"]["projection"].get("row_codec", LEGACY_ROW_CODEC))
                if role == "status":
                    status_digests = {key: value.get(key) for key in ("report_digest", "checkpoint_digest")}
                headers[role] = {key: deepcopy(value.get(key)) for key in
                    ("generation_id", "generation_schema", "sequence", "source_watermark", "configuration_fingerprint", "as_of", "safety")}
                verified[role] = proof
                if role in requested:
                    payloads[role] = value
                del value, member
            if any(value != cross_hashes for value in declared_cross.values()):
                raise ValueError("SHADOW_GENERATION_CROSS_HASH_MISMATCH")
            if (status_digests.get("report_digest") != verified["report"]["payload_digest"]
                    or status_digests.get("checkpoint_digest") != verified["checkpoint"]["payload_digest"]):
                raise ValueError("SHADOW_GENERATION_LOGICAL_MISMATCH")
            if "projection" in manifest["files"]:
                from .projection import open_projection, logical_digest
                member = self._bytes(generation / GENERATION_ROLES["projection"])
                if _sha(member) != manifest["files"]["projection"]["sha256"]:
                    raise ValueError("SHADOW_GENERATION_FILE_HASH_MISMATCH")
                connection, header = open_projection(member)
                try:
                    actual_hash, actual_size = logical_digest(connection, header)
                    if (header != expected_header or actual_hash != expected_projection["payload_digest"]
                            or actual_hash != manifest["files"]["projection"]["payload_digest"]):
                        raise ValueError("SHADOW_PROJECTION_DERIVATION_MISMATCH")
                finally:
                    connection.close()
                _semantic_safety({"projection": header})
                verified["projection"] = {"payload_digest": actual_hash, "logical_bytes": actual_size,
                    "storage_schema": header["schema"]}
                headers["projection"] = {key: deepcopy(header[key]) for key in
                    ("generation_id", "generation_schema", "sequence", "source_watermark", "configuration_fingerprint", "as_of", "safety")}
                if "projection" in requested: payloads["projection"] = header
            if any(value != cross_hashes for value in declared_cross.values()):
                raise ValueError("SHADOW_GENERATION_CROSS_HASH_MISMATCH")
            if (status_digests.get("report_digest") != verified["report"]["payload_digest"]
                    or status_digests.get("checkpoint_digest") != verified["checkpoint"]["payload_digest"]):
                raise ValueError("SHADOW_GENERATION_LOGICAL_MISMATCH")
            report_as_of = headers["report"]["as_of"]
            if (any(value["as_of"] != report_as_of for value in headers.values())
                    or manifest.get("source_watermark", {}).get("as_of") != report_as_of):
                raise ValueError("SHADOW_GENERATION_LOGICAL_MISMATCH")
            if len({digest(value) for value in declared_limits}) > 1:
                raise ValueError("SHADOW_GENERATION_SAFETY_MISMATCH")
            if not legacy and (digest(manifest.get("safety")) != digest(SAFETY) or manifest.get("as_of") != report_as_of):
                raise ValueError("SHADOW_GENERATION_SAFETY_MISMATCH")
            self._validate_authority(pointer, allow_legacy=allow_legacy, recovering=_recovering)
            failure = self._independent("failure.json") if not allow_degraded else None
            if failure and (not isinstance(failure, dict) or type(failure.get("observed_sequence")) is not int):
                raise ValueError("SHADOW_FAILURE_DIAGNOSTIC_INVALID")
            if failure and (failure["observed_sequence"] >= pointer["sequence"]):
                raise ValueError("SHADOW_GENERATION_DEGRADED:" + str(failure.get("reason", "FAIL_CLOSED")))
            return {**payloads, "manifest": manifest, "pointer": pointer,
                "export_contract": {"schema": EXPORT_SCHEMA, "generation_schema": pointer["schema"],
                    "generation_id": pointer["generation_id"], "sequence": pointer["sequence"],
                    "as_of": report_as_of, "source_watermark": manifest["source_watermark"],
                    "configuration_fingerprint": manifest["configuration_fingerprint"],
                    "safety": deepcopy(SAFETY), "legacy_bootstrap": legacy,
                    "verified_payloads": verified, "role_headers": headers, "returned_roles": list(requested),
                    "verification_level": "FULL_LOGICAL_SEMANTICS",
                    "custody": "LOCAL_DURABLE_CUSTODY_NOT_EXTERNAL_AUTHENTICATION"}}

    def read(self, name):
        if name in LOGICAL_ROLES:
            bundle = self.read_generation()
            return bundle[LOGICAL_ROLES[name]] if bundle else None
        return self._independent(name)

    def _wire_generation(self, *, deadline=None, checkpoint=False, allow_degraded=False, _pipeline_wire=False):
        """Verify sealed bytes/CRC/codec plus typed producer headers.

        This explicitly does not claim to recompute the large native report's
        source semantics. The publisher checks those before sealing custody.
        """
        def guard():
            if deadline is not None and time.monotonic() >= deadline:
                raise ValueError("SHADOW_PROJECTION_QUERY_DEADLINE")
        guard()
        pointer = self._pointer()
        if not pointer: raise ValueError("SHADOW_GENERATION_NOT_COMMITTED")
        self._validate_authority(pointer)
        if pointer["schema"] != GENERATION_SCHEMA: raise ValueError("SHADOW_GENERATION_LEGACY_REQUIRES_FORWARD_BOOTSTRAP")
        generation = self.root / ("gen-"+pointer["generation_id"])
        raw = self._bytes(generation / "manifest.json", limit=64*1024)
        if _sha(raw) != pointer["manifest_sha256"]: raise ValueError("SHADOW_GENERATION_MANIFEST_HASH_MISMATCH")
        manifest = _json(raw)
        if (manifest.get("schema") != GENERATION_SCHEMA or manifest.get("generation_id") != pointer["generation_id"]
                or type(manifest.get("sequence")) is not int or manifest["sequence"] != pointer["sequence"]
                or set(manifest.get("files", {})) != set(GENERATION_ROLES)
                or set(manifest.get("role_headers", {})) != set(GENERATION_ROLES)):
            raise ValueError("SHADOW_GENERATION_MANIFEST_MISMATCH")
        headers = manifest["role_headers"]
        _semantic_safety(headers)
        for header in headers.values():
            if (header.get("generation_id") != pointer["generation_id"] or header.get("generation_schema") != GENERATION_SCHEMA
                    or type(header.get("sequence")) is not int or header["sequence"] != pointer["sequence"]
                    or header.get("as_of") != manifest.get("as_of")
                    or header.get("source_watermark") != manifest.get("source_watermark")
                    or header.get("configuration_fingerprint") != manifest.get("configuration_fingerprint")):
                raise ValueError("SHADOW_GENERATION_MEMBER_MISMATCH")
        if (manifest.get("source_watermark", {}).get("as_of") != manifest.get("as_of")
                or digest(manifest.get("safety")) != digest(SAFETY)):
            raise ValueError("SHADOW_GENERATION_SAFETY_MISMATCH")
        verified, result, pending_receipts, pending_wire = {}, {"manifest": manifest, "pointer": pointer}, {}, []
        for role, name in GENERATION_ROLES.items():
            guard()
            record = manifest["files"][role]
            if record.get("name") != name: raise ValueError("SHADOW_GENERATION_PATH_MISMATCH")
            member = self._bytes(generation / name)
            member_sha = _sha(member)
            if member_sha != record.get("sha256"): raise ValueError("SHADOW_GENERATION_FILE_HASH_MISMATCH")
            if role == "projection":
                result["projection_bytes"] = member
                verified[role] = {key: record[key] for key in ("payload_digest", "logical_bytes", "storage_schema")}
                continue
            # This is a private receipt for the identical immutable bytes
            # already verified in this writer transaction, never a decoded
            # checkpoint or a reader cache. Every use still opens and hashes
            # the source member and rechecks CURRENT/custody/headers/deadline.
            receipt_key = (pointer["manifest_sha256"], role, member_sha,
                           self.payload_limit, EXPANDED_PAYLOAD_LIMIT)
            reusable = self.lock is not None and role != "status" and not (role == "checkpoint" and checkpoint)
            cached = self._wire_receipts.get(receipt_key) if reusable else None
            if cached is not None:
                verified[role] = deepcopy(cached)
                continue
            wire = member
            if name.endswith(".gz"):
                try:
                    with gzip.GzipFile(fileobj=io.BytesIO(member)) as stream: wire = stream.read(self.payload_limit+1)
                except (OSError, EOFError, zlib.error) as error:
                    raise ValueError("SHADOW_GENERATION_GZIP_INVALID") from error
                if len(wire) > self.payload_limit: raise ValueError("SHADOW_PAYLOAD_LIMIT")
            envelope = _json(wire)
            stored = envelope.get("payload") if isinstance(envelope, dict) else None
            if isinstance(stored, dict) and stored.get("schema") == STORAGE_SCHEMA:
                if role == "status" or role == "checkpoint" and checkpoint:
                    value = decode_storage(stored, durable_limit=self.payload_limit,
                                           expansion_limit=EXPANDED_PAYLOAD_LIMIT, deadline=deadline)
                    proof = {"payload_digest": stored["logical_sha256"], "logical_bytes": stored["logical_bytes"],
                             "storage_schema": stored["schema"]}
                else:
                    if _pipeline_wire:
                        proof = {"payload_digest": stored.get("logical_sha256"), "logical_bytes": stored.get("logical_bytes"),
                                 "storage_schema": stored.get("schema")}
                        pending_wire.append((stored, proof))
                    else:
                        proof = verify_storage_wire(stored, durable_limit=self.payload_limit,
                                                    expansion_limit=EXPANDED_PAYLOAD_LIMIT, deadline=deadline)
                    value = None
            else:
                value, proof = self._payload(name, member, details=True)
            if value is not None:
                _semantic_safety({role: value})
                if role == "report": _semantic_sources(value)
                if any(value.get(key) != header_value for key, header_value in headers[role].items()):
                    raise ValueError("SHADOW_GENERATION_MEMBER_MISMATCH")
            if envelope.get("digest") != proof["payload_digest"] or record.get("payload_digest") != proof["payload_digest"]:
                raise ValueError("SHADOW_EVIDENCE_DIGEST_MISMATCH")
            verified[role] = {**proof, "durable_bytes": len(member), "wire_bytes": len(wire)}
            if self.lock is not None and role != "status":
                pending_receipts[receipt_key] = deepcopy(verified[role])
            if role == "status" or (role == "checkpoint" and checkpoint): result[role] = value
        if pending_wire:
            # Two independent roles and one bounded SHA stream per role. All
            # work is joined before exposing a cut; no data/proof is cached.
            with ThreadPoolExecutor(max_workers=min(2, len(pending_wire)), thread_name_prefix="rc6-shadow-wire") as executor:
                futures = [executor.submit(verify_storage_wire, stored, durable_limit=self.payload_limit,
                                           expansion_limit=EXPANDED_PAYLOAD_LIMIT, deadline=deadline, _pipeline_hash=True)
                           for stored, _ in pending_wire]
                for (_, proof), future in zip(pending_wire, futures):
                    if future.result() != proof:
                        raise ValueError("SHADOW_EVIDENCE_DIGEST_MISMATCH")
            guard()
        status = result["status"]
        if (status.get("report_digest") != verified["report"]["payload_digest"]
                or status.get("checkpoint_digest") != verified["checkpoint"]["payload_digest"]
                or any(headers[role].get("cross_payload_hashes") != status.get("cross_payload_hashes") for role in ROLES)):
            raise ValueError("SHADOW_GENERATION_CROSS_HASH_MISMATCH")
        if not allow_degraded:
            failure = self._independent("failure.json")
            if failure and (type(failure.get("observed_sequence")) is not int or failure["observed_sequence"] >= pointer["sequence"]):
                raise ValueError("SHADOW_GENERATION_DEGRADED")
        guard()
        result["export_contract"] = {"generation_id": pointer["generation_id"], "generation_schema": GENERATION_SCHEMA,
            "sequence": pointer["sequence"], "as_of": manifest["as_of"], "source_watermark": manifest["source_watermark"],
            "configuration_fingerprint": manifest["configuration_fingerprint"], "safety": deepcopy(SAFETY),
            "verified_payloads": verified, "role_headers": headers,
            "custody": "LOCAL_DURABLE_CUSTODY_NOT_EXTERNAL_AUTHENTICATION"}
        if self.lock is not None:
            self._wire_receipts = {key: value for key, value in self._wire_receipts.items()
                                   if key[0] == pointer["manifest_sha256"]}
            self._wire_receipts.update(pending_receipts)
        return result

    def read_writer_generation(self, *, checkpoint):
        """Reuse sealed producer validation, decode only checkpoint for restart.

        Old three-role cuts use the strict full reader until their next forward
        publication. This internal writer route never advertises FULL_LOGICAL.
        """
        pointer = self._pointer()
        if not pointer:
            self._validate_authority(pointer, allow_legacy=True); return None
        raw = _json(self._bytes(self.root / ("gen-"+pointer["generation_id"]) / "manifest.json", limit=64*1024))
        if "projection" not in raw.get("files", {}):
            return self.read_generation(allow_degraded=True, allow_legacy=True, roles=("checkpoint",) if checkpoint else ())
        with self._reader():
            bundle = self._wire_generation(checkpoint=checkpoint, allow_degraded=True)
            from .projection import open_projection
            connection, header = open_projection(bundle.pop("projection_bytes"))
            connection.close()
            if (header.get("derivation") != {role: bundle["manifest"]["files"][role]["payload_digest"] for role in ROLES}
                    or any(header.get(key) != bundle["export_contract"].get(key) for key in
                           ("generation_id", "generation_schema", "sequence", "as_of", "source_watermark", "configuration_fingerprint", "safety"))):
                raise ValueError("SHADOW_PROJECTION_DERIVATION_MISMATCH")
            _semantic_safety({"projection": header})
            bundle["export_contract"]["verification_level"] = "WIRE_AND_CHECKPOINT_SEMANTICS" if checkpoint else "SEALED_WIRE_CUSTODY"
            return bundle

    def _fault(self, stage):
        if self.fault_inject is not None:
            self.fault_inject(stage)

    def _sync_directory(self, path):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _durable_member(self, path, data):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            self._fault("before_fsync")
            os.fsync(stream.fileno())
            self._fault("after_fsync")

    def _retention(self, *, additional_bytes, additional_files, pinned=()):
        # The sole writer owns quota checks/rotation. Independent readers take
        # a shared nonblocking flock, preventing deleted-generation races.
        from .retention import EvidenceRetention
        self._validate_authority(self._pointer(), allow_legacy=True)
        return EvidenceRetention(self.root, maximum_bytes=self.maximum_bytes, maximum_files=self.maximum_files,
            archive_root=self.archive_root, archive_maximum_bytes=self.archive_maximum_bytes,
            auto_archive=self.archive_root is not None,
            fault_inject=self.fault_inject).prepare(
            additional_bytes=additional_bytes, additional_files=additional_files, pinned=pinned,
            writer_fd=self.lock.fileno())

    def commit_generation(self, report, checkpoint, status, *, source_watermark,
                          configuration_fingerprint, _take_payloads=False):
        if self.lock is None:
            raise ValueError("SHADOW_WRITER_LOCK_REQUIRED")
        before = self.read_writer_generation(checkpoint=False)
        pointer = before["pointer"] if before else {}
        authority = self._authority() or {"allocated_sequence": pointer.get("sequence", 0),
                                         "committed": pointer or None, "prepared": None}
        if pointer and pointer == authority.get("prepared"):
            authority.update(committed=pointer, prepared=None, phase="COMMITTED")
            self._write_authority(authority)
        sequence = authority["allocated_sequence"] + 1
        ident = uuid.uuid4().hex
        metadata = dict(generation_id=ident, generation_schema=GENERATION_SCHEMA, sequence=sequence,
                        source_watermark=deepcopy(source_watermark), configuration_fingerprint=configuration_fingerprint)
        # The single worker transfers freshly produced nested values. Public
        # callers retain the defensive copy; only top-level metadata is edited.
        copier = dict if _take_payloads else deepcopy
        values = {"report": copier(report), "checkpoint": copier(checkpoint), "status": copier(status)}
        for value in values.values():
            # Existing generation metadata is never copied from a prior cut.
            value.pop("cross_payload_hashes", None)
            value.update(metadata)
        if (not isinstance(source_watermark, dict) or not isinstance(configuration_fingerprint, str)
                or not configuration_fingerprint or any(value.get("as_of") != source_watermark.get("as_of")
                                                  for value in values.values())):
            raise ValueError("SHADOW_GENERATION_INPUT_MISMATCH")
        # Empty legacy caller reports get the canonical empty audit explicitly;
        # nonempty or misleading audits are never silently repaired.
        if values["report"].get("source_reports", []) == [] and values["report"].get("source_audit", {}) == {}:
            values["report"]["source_reports"] = []
            values["report"]["source_audit"] = audit_sources(reports=[], as_of=values["report"]["as_of"])
        _semantic_sources(values["report"])
        reports = values["report"].get("source_reports", [])
        _semantic_safety(values, fill=True)
        stamp(values["report"]["as_of"])
        mutable = {"cross_payload_hashes", "evidence_retention", "status", "observation_status",
                   "report_digest", "checkpoint_digest"}
        cache, shape_memo = {}, {}
        prepared = {role: PreparedStorage(value, mutable=mutable, durable_limit=self.payload_limit-128,
                                         expansion_limit=EXPANDED_PAYLOAD_LIMIT, cache=cache, shape_memo=shape_memo) for role, value in values.items()}
        shape_memo.clear()
        from .projection import PreparedProjection
        projection_builder = PreparedProjection(values["report"])
        def pack():
            for value in values.values():
                value.pop("cross_payload_hashes", None)
            hashes = {role: prepared[role].metrics(values[role])[0]
                      for role in ("report", "checkpoint")}
            for value in values.values():
                value["cross_payload_hashes"] = hashes
            encoded, payload_digests = {}, {}
            for role, name in ROLES.items():
                if role == "status":
                    values[role].update(report_digest=payload_digests["report"], checkpoint_digest=payload_digests["checkpoint"])
                representation, payload_digests[role] = prepared[role].encode(values[role])
                wire = _encode({"digest": payload_digests[role], "payload": representation})
                if len(wire) > self.payload_limit:
                    raise ValueError("SHADOW_PAYLOAD_LIMIT")
                encoded[role] = gzip.compress(wire, mtime=0, compresslevel=1) if name.endswith(".gz") else wire
            manifest = {"schema": GENERATION_SCHEMA, **metadata, "as_of": values["report"]["as_of"],
                "safety": deepcopy(SAFETY),
                "previous_generation_id": pointer.get("generation_id"),
                "previous_manifest_sha256": pointer.get("manifest_sha256"),
                "source_audit_digest": digest(values["report"].get("source_audit", {})),
                "source_reports_digest": digest(reports),
                "files": {role: {"name": name, "sha256": _sha(encoded[role]),
                                 "payload_digest": payload_digests[role]} for role, name in ROLES.items()}}
            projection_data, projection_header, projection_proof = projection_builder.build(values["report"], payload_digests)
            encoded["projection"] = projection_data
            manifest["files"]["projection"] = {"name": GENERATION_ROLES["projection"], "sha256": _sha(projection_data),
                **projection_proof}
            manifest["role_headers"] = {role: {key: deepcopy(value.get(key)) for key in (*SAFETY,
                "safety", "generation_id", "generation_schema", "sequence", "as_of", "source_watermark",
                "configuration_fingerprint", "cross_payload_hashes", "production_limits_modified") if key in value}
                for role, value in values.items()}
            manifest["role_headers"]["projection"] = {key: deepcopy(projection_header.get(key)) for key in (*SAFETY,
                "safety", "generation_id", "generation_schema", "sequence", "as_of", "source_watermark", "configuration_fingerprint")}
            manifest_data = _encode(manifest)
            current = {"schema": GENERATION_SCHEMA, "generation_id": ident,
                       "sequence": manifest["sequence"], "manifest_sha256": _sha(manifest_data)}
            current["digest"] = digest(current)
            return encoded, manifest, manifest_data, current, _encode(current)

        encoded, manifest, manifest_data, current, current_data = pack()
        # Worst-case peak: both CURRENT versions + the new directory and all
        # four members. Quota reservation precedes creation of any staging.
        pinned = [pointer.get("generation_id"), (before or {}).get("manifest", {}).get("previous_generation_id")]
        reserved = sum(map(len, encoded.values())) + len(manifest_data) + len(current_data) + 8192 + 4096
        metrics = self._retention(additional_bytes=reserved,
                                  additional_files=7, pinned=[p for p in pinned if p])
        # Persist the alert in this same logical cut. Reserve a bounded amount
        # for its telemetry before writing anything; no quota surprise at commit.
        values["report"]["evidence_retention"] = metrics
        values["status"]["evidence_retention"] = metrics
        if metrics.get("status") == "RETENTION_PRESSURE":
            values["report"]["observation_status"] = values["report"].get("status")
            values["status"]["observation_status"] = values["status"].get("status")
            values["report"]["status"] = values["status"]["status"] = "RETENTION_PRESSURE"
        encoded, manifest, manifest_data, current, current_data = pack()
        if sum(map(len, encoded.values())) + len(manifest_data) + len(current_data) > reserved:
            raise ValueError("SHADOW_RETENTION_RESERVATION_EXCEEDED")
        projection_builder.close()
        prepared.clear()
        cache.clear()
        # Persist reservation before any sequence-bearing generation exists.
        # A killed preparation may leave a gap, never a reused sequence/fork.
        authority.update(allocated_sequence=sequence, prepared=current, phase="PREPARED")
        self._write_authority(authority)
        staging = self.root / (".generation-" + ident + ".tmp")
        final = self.root / ("gen-" + ident)
        # UUID collisions, including after clock rollback, fail closed rather
        # than replacing any evidence. Wall clock never supplies the identity.
        staging.mkdir(mode=0o700)
        pointer_tmp = self.root / (".CURRENT." + ident + ".tmp")
        try:
            for role, name in GENERATION_ROLES.items():
                self._durable_member(staging / name, encoded[role])
                self._fault("after_" + role)
            self._durable_member(staging / "manifest.json", manifest_data)
            self._sync_directory(staging)
            if final.exists() or final.is_symlink():
                raise ValueError("SHADOW_GENERATION_ID_REUSED")
            staging.rename(final)
            self._sync_directory(self.root)
            self._durable_member(pointer_tmp, current_data)
            self._fault("before_commit_pointer")
            authority["phase"] = "PUBLISHING"
            self._write_authority(authority)
            self._fault("after_publication_intent")
            pointer_tmp.replace(self.path("CURRENT.json"))
            self._fault("after_current_replace")
            self._sync_directory(self.root)
            self._fault("before_lineage_seal")
            authority.update(committed=current, prepared=None, phase="COMMITTED")
            self._write_authority(authority)
            self._fault("after_commit_pointer")
        finally:
            # Only our exact newly allocated temporary names may be removed.
            # Final dirs are preserved, including an orphan before CURRENT.
            pointer_tmp.unlink(missing_ok=True)
            if staging.exists() and not staging.is_symlink():
                for name in (*GENERATION_ROLES.values(), "manifest.json"):
                    (staging / name).unlink(missing_ok=True)
                staging.rmdir()
        self.last_retention_metrics = metrics
        return {**values, "manifest": manifest, "pointer": current}

    def record_failure(self, *, as_of, error):
        """Separate degraded health; never overwrite status of a committed cut."""
        pointer = self._pointer() or {}
        reason = failure_reason(error)
        payload = {"schema": "rc6.shadow-evidence-failure.v1", "as_of": str(as_of),
            "status": "FAIL_CLOSED", "shadow_degraded": True, "reason": reason,
            "error_class": type(error).__name__, "observed_generation_id": pointer.get("generation_id"),
            "observed_sequence": pointer.get("sequence", 0),
            "retention": getattr(error, "metrics", {}), "real_orders_sent": 0,
            "real_routes": "NOT_CALLED", "provider_requests": 0}
        self.write("failure.json", payload)
        return payload

    def write(self, name, payload, *, immutable=False):
        if self.lock is None:
            raise ValueError("SHADOW_WRITER_LOCK_REQUIRED")
        if name in LOGICAL_ROLES or name == "CURRENT.json" or name.startswith("gen-"):
            raise ValueError("SHADOW_GENERATION_COMMIT_REQUIRED")
        target = self.path(name)
        if immutable and target.exists():
            existing = self.read(name)
            if digest(existing) != digest(payload):
                raise ValueError("SHADOW_IMMUTABLE_FREEZE_CONFLICT")
            return
        encoded = _encode({"digest": digest(payload), "payload": payload})
        if len(encoded) > self.payload_limit:
            raise ValueError("SHADOW_PAYLOAD_LIMIT")
        data = gzip.compress(encoded, mtime=0, compresslevel=1) if name.endswith(".gz") else encoded
        self._retention(additional_bytes=len(data), additional_files=1)
        tmp = self.root / (".independent-" + uuid.uuid4().hex + ".tmp")
        try:
            self._durable_member(tmp, data)
            tmp.replace(target)
            self._sync_directory(self.root)
        finally:
            tmp.unlink(missing_ok=True)


def read_committed_generation(root, *, payload_limit=64 * 1024**2, roles=None):
    """Read-only, bounded, nonblocking external consumer of the whole cut."""
    value = EvidenceFiles(root, payload_limit=payload_limit).read_generation(roles=roles)
    if value is None:
        raise ValueError("SHADOW_GENERATION_NOT_COMMITTED")
    return value


def read_committed_projection(root, *, filters=None, offset=0, limit=10, deadline=None):
    """Bounded read-only pages from all rows of the same sealed generation.

    Wire/CRC/custody verification is explicit; full logical decode remains
    available through read_committed_generation for offline reaudits.
    """
    if (type(offset) is not int or not 0 <= offset <= 100000 or type(limit) is not int or not 1 <= limit <= 10
            or filters is not None and not isinstance(filters, dict)):
        raise ValueError("SHADOW_PROJECTION_QUERY_INVALID")
    from .projection import open_projection, query_projection, EXPORT_SCHEMA as PROJECTION_EXPORT, LEVEL
    files = EvidenceFiles(root)
    with files._reader():
        bundle = files._wire_generation(deadline=deadline, _pipeline_wire=True)
        connection, header = open_projection(bundle.pop("projection_bytes"))
        try:
            expected = {role: bundle["manifest"]["files"][role]["payload_digest"] for role in ROLES}
            if (header.get("derivation") != expected or any(header.get(key) != bundle["export_contract"].get(key) for key in
                    ("generation_id", "generation_schema", "sequence", "as_of", "source_watermark", "configuration_fingerprint", "safety"))):
                raise ValueError("SHADOW_PROJECTION_DERIVATION_MISMATCH")
            _semantic_safety({"projection": header})
            pages, funnel = query_projection(connection, header, filters=filters or {}, offset=offset, limit=limit, deadline=deadline)
            bundle.update(report=header["report"], dataset_pages=pages, funnel_scope=funnel)
            bundle["export_contract"].update(schema=PROJECTION_EXPORT, verification_level=LEVEL, derivation=expected)
            if len(_encode(bundle)) > 4*1024**2: raise ValueError("SHADOW_PROJECTION_QUERY_BYTE_LIMIT")
            if deadline is not None and time.monotonic() >= deadline: raise ValueError("SHADOW_PROJECTION_QUERY_DEADLINE")
            return bundle
        finally:
            connection.close()
