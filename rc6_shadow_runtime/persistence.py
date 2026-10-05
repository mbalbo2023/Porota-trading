"""Durable SHADOW generations; CURRENT is the only publication authority.

Report/checkpoint/status are immutable, hash-linked members of one generation.
Standalone preopen freezes and failure diagnostics have separate explicit roles.
No reader reconstructs a snapshot from file mtimes or independently written JSON.
"""
from contextlib import contextmanager
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
import uuid
import zlib

from rc6_dynamic_universe.common import digest, stamp
from rc6_dynamic_universe.sources import audit_sources, sanitize_source_errors

GENERATION_SCHEMA = "rc6.shadow-evidence-generation.v2"
LEGACY_GENERATION_SCHEMA = "rc6.shadow-evidence-generation.v1"
EXPORT_SCHEMA = "rc6.shadow-committed-cut.v2"
LINEAGE_SCHEMA = "rc6.shadow-lineage-authority.v2"
DEFAULT_MAXIMUM_FILES = 8192
SAFETY = {"mode": "SHADOW", "real_orders_sent": 0, "real_routes": "NOT_CALLED",
          "provider_requests": 0, "source_database_effect": "READ_ONLY",
          "factual_execution": "NOT_CALLED", "ppi_watch": "UNTOUCHED"}
ROLES = {"report": "report.json.gz", "checkpoint": "checkpoint.json.gz", "status": "status.json"}
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


def _semantic_sources(report, *, legacy=False):
    reports, audit = report.get("source_reports", []), report.get("source_audit", {})
    if not isinstance(reports, list) or not isinstance(audit, dict):
        raise ValueError("SHADOW_SOURCE_AUDIT_INVALID")
    if reports and audit.get("source_reports_digest") != digest(reports):
        raise ValueError("SHADOW_SOURCE_AUDIT_DIGEST_MISMATCH")
    if not legacy and reports != sanitize_source_errors(reports):
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
        if "safety" in value and value["safety"] != SAFETY:
            raise ValueError("SHADOW_GENERATION_SAFETY_MISMATCH")
        if fill:
            value.update(deepcopy(SAFETY))
            value["safety"] = deepcopy(SAFETY)
        elif value.get("safety") != SAFETY or any(value.get(key) != expected for key, expected in SAFETY.items()):
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

    @contextmanager
    def _reader(self):
        # Readers never mkdir/create files and never wait behind expensive
        # SHADOW serialization. Factual callers may immediately fall back.
        if self.lock is not None:
            yield
            return
        fd = os.open(self.path("writer.lock"), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
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
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
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

    def _payload(self, name, raw):
        if name.endswith(".gz"):
            try:
                with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                    raw = stream.read(self.payload_limit + 1)
            except (OSError, EOFError, zlib.error) as error:
                raise ValueError("SHADOW_GENERATION_GZIP_INVALID") from error
            if len(raw) > self.payload_limit:
                raise ValueError("SHADOW_PAYLOAD_LIMIT")
        envelope = _json(raw)
        if not isinstance(envelope, dict) or envelope.get("digest") != digest(envelope.get("payload")):
            raise ValueError("SHADOW_EVIDENCE_DIGEST_MISMATCH")
        return envelope["payload"]

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
                    or not ID_PATTERN.fullmatch(str(head.get("generation_id", "")))
                    or head.get("digest") != digest({k: v for k, v in head.items() if k != "digest"})):
                raise ValueError("SHADOW_LINEAGE_AUTHORITY_INVALID")
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

    def _validate_authority(self, pointer, *, allow_legacy=False):
        authority = self._authority()
        if authority is None:
            if pointer is None or (allow_legacy and pointer["schema"] == LEGACY_GENERATION_SCHEMA):
                return None
            raise ValueError("SHADOW_LINEAGE_ANCHOR_MISSING")
        if pointer is None and authority.get("committed") is None:
            return authority
        if pointer is not None and (pointer == authority.get("committed") or pointer == authority.get("prepared")):
            return authority
        committed = authority.get("committed") or {}
        if pointer is None or pointer.get("sequence", 0) < committed.get("sequence", 0):
            raise ValueError("SHADOW_CURRENT_ROLLBACK")
        raise ValueError("SHADOW_LINEAGE_FORK_OR_REHASH")

    def read_generation(self, *, allow_degraded=False, allow_legacy=False):
        """Read one committed cut and verify every member, even for one role.

        A coherent older CURRENT remains a coherent older cut, never a new cut.
        Consumers enforce source/as_of freshness; mtime is never authoritative.
        """
        with self._reader():
            pointer = self._pointer()
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
                    or set(manifest.get("files", {})) != set(ROLES)):
                raise ValueError("SHADOW_GENERATION_MANIFEST_MISMATCH")
            payloads = {}
            for role, name in ROLES.items():
                record = manifest["files"][role]
                if not isinstance(record, dict) or record.get("name") != name:
                    raise ValueError("SHADOW_GENERATION_PATH_MISMATCH")
                member = self._bytes(generation / name)
                if _sha(member) != record.get("sha256"):
                    raise ValueError("SHADOW_GENERATION_FILE_HASH_MISMATCH")
                value = self._payload(name, member)
                if (not isinstance(value, dict) or digest(value) != record.get("payload_digest")
                        or value.get("generation_id") != pointer["generation_id"]
                        or value.get("source_watermark") != manifest.get("source_watermark")
                        or value.get("configuration_fingerprint") != manifest.get("configuration_fingerprint")):
                    raise ValueError("SHADOW_GENERATION_MEMBER_MISMATCH")
                payloads[role] = value
                if not legacy and (value.get("generation_schema") != GENERATION_SCHEMA
                        or type(value.get("sequence")) is not int or value.get("sequence") != pointer["sequence"]):
                    raise ValueError("SHADOW_GENERATION_MEMBER_MISMATCH")
            hashes = {role: digest({k: v for k, v in payloads[role].items() if k != "cross_payload_hashes"})
                      for role in ("report", "checkpoint")}
            if any(value.get("cross_payload_hashes") != hashes for value in payloads.values()):
                raise ValueError("SHADOW_GENERATION_CROSS_HASH_MISMATCH")
            status, report, checkpoint = payloads["status"], payloads["report"], payloads["checkpoint"]
            if (status.get("report_digest") != digest(report) or status.get("checkpoint_digest") != digest(checkpoint)
                    or any(value.get("as_of") != report.get("as_of") for value in payloads.values())
                    or manifest.get("source_watermark", {}).get("as_of") != report.get("as_of")
                    or manifest.get("source_audit_digest") != digest(report.get("source_audit", {}))
                    or manifest.get("source_reports_digest") != digest(report.get("source_reports", []))):
                raise ValueError("SHADOW_GENERATION_LOGICAL_MISMATCH")
            _semantic_sources(report, legacy=legacy)
            _semantic_safety(payloads, fill=legacy)
            if not legacy and (manifest.get("safety") != SAFETY or manifest.get("as_of") != report["as_of"]):
                raise ValueError("SHADOW_GENERATION_SAFETY_MISMATCH")
            self._validate_authority(pointer, allow_legacy=allow_legacy)
            failure = self._independent("failure.json") if not allow_degraded else None
            if failure and (not isinstance(failure, dict) or type(failure.get("observed_sequence")) is not int):
                raise ValueError("SHADOW_FAILURE_DIAGNOSTIC_INVALID")
            if failure and (failure["observed_sequence"] >= pointer["sequence"]):
                raise ValueError("SHADOW_GENERATION_DEGRADED:" + str(failure.get("reason", "FAIL_CLOSED")))
            return {**payloads, "manifest": manifest, "pointer": pointer,
                "export_contract": {"schema": EXPORT_SCHEMA, "generation_schema": pointer["schema"],
                    "generation_id": pointer["generation_id"], "sequence": pointer["sequence"],
                    "as_of": report["as_of"], "source_watermark": manifest["source_watermark"],
                    "configuration_fingerprint": manifest["configuration_fingerprint"],
                    "safety": deepcopy(SAFETY), "legacy_bootstrap": legacy,
                    "custody": "LOCAL_DURABLE_CUSTODY_NOT_EXTERNAL_AUTHENTICATION"}}

    def read(self, name):
        if name in LOGICAL_ROLES:
            bundle = self.read_generation()
            return bundle[LOGICAL_ROLES[name]] if bundle else None
        return self._independent(name)

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
                          configuration_fingerprint):
        if self.lock is None:
            raise ValueError("SHADOW_WRITER_LOCK_REQUIRED")
        before = self.read_generation(allow_degraded=True, allow_legacy=True)
        pointer = before["pointer"] if before else {}
        authority = self._authority() or {"allocated_sequence": pointer.get("sequence", 0),
                                         "committed": pointer or None, "prepared": None}
        if pointer and pointer == authority.get("prepared"):
            authority.update(committed=pointer, prepared=None)
            self._write_authority(authority)
        sequence = authority["allocated_sequence"] + 1
        ident = uuid.uuid4().hex
        metadata = dict(generation_id=ident, generation_schema=GENERATION_SCHEMA, sequence=sequence,
                        source_watermark=deepcopy(source_watermark), configuration_fingerprint=configuration_fingerprint)
        values = {"report": deepcopy(report), "checkpoint": deepcopy(checkpoint), "status": deepcopy(status)}
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
        def pack():
            for value in values.values():
                value.pop("cross_payload_hashes", None)
            hashes = {role: digest(values[role]) for role in ("report", "checkpoint")}
            for value in values.values():
                value["cross_payload_hashes"] = hashes
            values["status"].update(report_digest=digest(values["report"]),
                                   checkpoint_digest=digest(values["checkpoint"]))
            encoded = {}
            for role, name in ROLES.items():
                wire = _encode({"digest": digest(values[role]), "payload": values[role]})
                if len(wire) > self.payload_limit:
                    raise ValueError("SHADOW_PAYLOAD_LIMIT")
                encoded[role] = gzip.compress(wire, mtime=0) if name.endswith(".gz") else wire
            manifest = {"schema": GENERATION_SCHEMA, **metadata, "as_of": values["report"]["as_of"],
                "safety": deepcopy(SAFETY),
                "previous_generation_id": pointer.get("generation_id"),
                "previous_manifest_sha256": pointer.get("manifest_sha256"),
                "source_audit_digest": digest(values["report"].get("source_audit", {})),
                "source_reports_digest": digest(reports),
                "files": {role: {"name": name, "sha256": _sha(encoded[role]),
                                 "payload_digest": digest(values[role])} for role, name in ROLES.items()}}
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
                                  additional_files=6, pinned=[p for p in pinned if p])
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
        # Persist reservation before any sequence-bearing generation exists.
        # A killed preparation may leave a gap, never a reused sequence/fork.
        authority.update(allocated_sequence=sequence, prepared=current)
        self._write_authority(authority)
        staging = self.root / (".generation-" + ident + ".tmp")
        final = self.root / ("gen-" + ident)
        # UUID collisions, including after clock rollback, fail closed rather
        # than replacing any evidence. Wall clock never supplies the identity.
        staging.mkdir(mode=0o700)
        pointer_tmp = self.root / (".CURRENT." + ident + ".tmp")
        try:
            for role, name in ROLES.items():
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
            pointer_tmp.replace(self.path("CURRENT.json"))
            self._sync_directory(self.root)
            self._fault("after_commit_pointer")
            authority.update(committed=current, prepared=None)
            self._write_authority(authority)
        finally:
            # Only our exact newly allocated temporary names may be removed.
            # Final dirs are preserved, including an orphan before CURRENT.
            pointer_tmp.unlink(missing_ok=True)
            if staging.exists() and not staging.is_symlink():
                for name in (*ROLES.values(), "manifest.json"):
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
        data = gzip.compress(encoded, mtime=0) if name.endswith(".gz") else encoded
        self._retention(additional_bytes=len(data), additional_files=1)
        tmp = self.root / (".independent-" + uuid.uuid4().hex + ".tmp")
        try:
            self._durable_member(tmp, data)
            tmp.replace(target)
            self._sync_directory(self.root)
        finally:
            tmp.unlink(missing_ok=True)


def read_committed_generation(root, *, payload_limit=64 * 1024**2):
    """Read-only, bounded, nonblocking external consumer of the whole cut."""
    value = EvidenceFiles(root, payload_limit=payload_limit).read_generation()
    if value is None:
        raise ValueError("SHADOW_GENERATION_NOT_COMMITTED")
    return value
