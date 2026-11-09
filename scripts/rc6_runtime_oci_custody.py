"""Fail-closed, exact-CID runtime custody for RC6's shared Docker daemon.

This module NEVER treats a name, label, image, or retrospective inspect as
birth evidence. The trusted root-owned receipts must be provisioned by the
independent host control plane, under the existing deploy mutex. Candidate
source with arbitrary root privileges is outside this authority's threat model.

No root receipt means HOLD79 *before* any Docker operation. In particular,
the module does not silently bootstrap existing legacy containers.
"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess


TRUSTED_ROOT = Path("/var/lib/porota-rc6-oci-custody")
ROOT_UID = 0
DOCKER_BINARY = "/usr/bin/docker"
DOCKER_ENDPOINT = "unix:///var/run/docker.sock"
MAX_RECEIPT_BYTES = 65536
ROLES = {
    "dashboard": "/porota_production_dashboard",
    "observer": "/porota_production_observer",
    "critical": "/porota_critical_approval_rc6",
}


def _birth_name(role, generation):
    """Fresh generation names cannot collide with legacy canonical names."""
    _require(role in ROLES and _is_token(generation, _HEX32), "BIRTH_NAME_IDENTITY")
    return "/porota_rc6_" + role + "_g_" + generation
_COMMON = {
    "schema_version", "candidate_sha", "candidate_tree", "image_id",
    "artifact_digest", "generation", "run_id", "run_attempt", "daemon_id",
    "paper_mode", "real_orders_allowed",
}
_CURRENT_KEYS = _COMMON | {"sealed", "roles", "birth_intent_sha256"}
_INTENT_KEYS = _COMMON | {"prior_receipt_sha256"}
_GENESIS_KEYS = _COMMON | {"state", "authority"}
_RECORD_KEYS = {"cid", "image_id", "created", "name"}
_CRITICAL_EXCLUSIVE_KEYS = {
    "schema_version", "state", "authority", "generation", "candidate_sha",
    "run_id", "run_attempt", "daemon_id", "image_id", "newborn_cid",
    "newborn_created", "attested_at", "legacy_critical_quiesced",
    "systemd_unit_quiesced", "single_consumer_exclusive",
}
_CID = re.compile(r"[0-9a-f]{64}\Z")
_HEX32 = re.compile(r"[0-9a-f]{32}\Z")
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_DECIMAL = re.compile(r"[1-9][0-9]*\Z")
_JOURNAL_PHASES = {"REQUESTED", "CONFIRMED"}
_JOURNAL_OPERATIONS = {"stop", "remove", "start", "exec"}


class CustodyHold(RuntimeError):
    """Typed no-side-effects gate; callers should exit with code 79."""

    exit_code = 79

    def __init__(self, reason):
        # Bounded fixed reason, never include host output, CID or credentials.
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,70}", reason):
            reason = "UNVERIFIED"
        super().__init__("RC6_OCI_CUSTODY_HOLD79:" + reason)
        self.reason = reason


def _require(condition, reason):
    if not condition:
        raise CustodyHold(reason)


def _strict_json(raw):
    _require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_RECEIPT_BYTES,
             "RECEIPT_SIZE")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def no_constant(_value):
        raise CustodyHold("JSON_NONFINITE")

    try:
        result = json.loads(raw.decode("utf-8", errors="strict"),
                            object_pairs_hook=unique, parse_constant=no_constant)
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise CustodyHold("JSON_INVALID") from exc
    _require(type(result) is dict, "RECEIPT_NOT_OBJECT")
    return result


def _read_regular_file(path, *, required_uid=ROOT_UID, limit=MAX_RECEIPT_BYTES):
    """Read a bounded inode via O_NOFOLLOW; reject aliases and shared writers."""
    path = Path(path)
    _require(path.is_absolute(), "RECEIPT_PATH")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise CustodyHold("RECEIPT_MISSING_OR_ALIAS") from exc
    try:
        meta = os.fstat(fd)
        _require(stat.S_ISREG(meta.st_mode) and meta.st_nlink == 1,
                 "RECEIPT_INODE")
        _require(meta.st_uid == required_uid and not meta.st_mode & 0o022,
                 "RECEIPT_OWNER_OR_MODE")
        _require(0 < meta.st_size <= limit, "RECEIPT_SIZE")
        raw = os.read(fd, limit + 1)
        _require(0 < len(raw) <= limit and len(raw) == meta.st_size,
                 "RECEIPT_READ_RACE")
        _require(os.fstat(fd).st_ino == meta.st_ino and
                 os.fstat(fd).st_size == meta.st_size, "RECEIPT_CHANGED")
        return raw
    finally:
        os.close(fd)


def _verify_root_directory():
    """Walk from / without following a symlink in any directory component."""
    _require(TRUSTED_ROOT.is_absolute(), "ROOT_PATH")
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open("/", flags)
    try:
        for component in TRUSTED_ROOT.parts[1:]:
            _require(component not in ("", ".", ".."), "ROOT_PATH")
            try:
                child = os.open(component, flags, dir_fd=fd)
            except OSError as exc:
                raise CustodyHold("ROOT_MISSING_OR_ALIAS") from exc
            os.close(fd)
            fd = child
            meta = os.fstat(fd)
            _require(stat.S_ISDIR(meta.st_mode) and meta.st_uid == ROOT_UID and
                     not meta.st_mode & 0o022, "ROOT_OWNER_OR_MODE")
        _require(os.fstat(fd).st_mode & 0o077 == 0, "ROOT_NOT_PRIVATE")
    finally:
        os.close(fd)


def _read_trusted_current():
    _verify_root_directory()
    return _strict_json(_read_regular_file(TRUSTED_ROOT / "current.json"))


def _read_trusted_intent():
    _verify_root_directory()
    return _strict_json(_read_regular_file(TRUSTED_ROOT / "intent.json"))


def _read_trusted_genesis():
    """External root control plane supplies this file; this module never does."""
    _verify_root_directory()
    return _strict_json(_read_regular_file(TRUSTED_ROOT / "genesis.json"))


@contextmanager
def _root_lock():
    _verify_root_directory()
    path = TRUSTED_ROOT / "lock"
    flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise CustodyHold("LOCK_MISSING_OR_ALIAS") from exc
    try:
        meta = os.fstat(fd)
        _require(stat.S_ISREG(meta.st_mode) and meta.st_nlink == 1 and
                 meta.st_uid == ROOT_UID and not meta.st_mode & 0o077,
                 "LOCK_OWNER_OR_MODE")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    except BlockingIOError as exc:
        raise CustodyHold("LOCK_BUSY") from exc
    finally:
        os.close(fd)


def _is_token(value, pattern):
    return type(value) is str and pattern.fullmatch(value) is not None


def _validate_common(doc, expected_keys):
    _require(type(doc) is dict and set(doc) == expected_keys, "RECEIPT_SCHEMA")
    _require(type(doc["schema_version"]) is int and doc["schema_version"] == 1,
             "RECEIPT_VERSION")
    _require(_is_token(doc["candidate_sha"], _HEX40) and
             _is_token(doc["candidate_tree"], _HEX40) and
             _is_token(doc["generation"], _HEX32) and
             _is_token(doc["image_id"], _DIGEST) and
             _is_token(doc["artifact_digest"], _DIGEST), "IDENTITY_FORMAT")
    _require(_is_token(doc["run_id"], _DECIMAL) and
             _is_token(doc["run_attempt"], _DECIMAL), "RUN_FORMAT")
    _require(type(doc["daemon_id"]) is str and
             6 <= len(doc["daemon_id"]) <= 128 and
             re.fullmatch(r"[A-Za-z0-9:_./-]+", doc["daemon_id"]), "DAEMON_FORMAT")
    _require(doc["paper_mode"] == "PRODUCTION_PAPER" and
             doc["real_orders_allowed"] is False, "PAPER_ONLY")


def _validate_current(doc):
    _validate_common(doc, _CURRENT_KEYS)
    _require(_is_token(doc["birth_intent_sha256"], _CID), "BIRTH_INTENT_MISSING")
    _require(doc["sealed"] is True and type(doc["roles"]) is dict and
             set(doc["roles"]) == set(ROLES), "THREE_ROLE_SEAL")
    found = set()
    for role in ROLES:
        record = doc["roles"][role]
        _require(type(record) is dict and set(record) == _RECORD_KEYS,
                 "BIRTH_RECORD_SCHEMA")
        cid = record["cid"]
        _require(_is_token(cid, _CID) and cid not in found, "BIRTH_CID")
        found.add(cid)
        _require(record["image_id"] == doc["image_id"] and
                 record["name"] == _birth_name(role, doc["generation"]) and
                 type(record["created"]) is str,
                 "BIRTH_ROLE_IDENTITY")
        try:
            birth_time = datetime.fromisoformat(record["created"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise CustodyHold("BIRTH_TIME_FORMAT") from exc
        _require(birth_time.tzinfo is not None, "BIRTH_TIME_FORMAT")


def _validate_intent(doc):
    _validate_common(doc, _INTENT_KEYS)
    _require(_is_token(doc["prior_receipt_sha256"], _CID), "PRIOR_HASH")


def _validate_genesis(doc):
    _validate_common(doc, _GENESIS_KEYS)
    _require(doc["state"] == "LEGACY_UNTRUSTED" and
             doc["authority"] == "INDEPENDENT_HOST_CONTROL_PLANE",
             "GENESIS_NOT_EXTERNALLY_ATTESTED")


def _canonical_digest(doc):
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


def _assert_continuity(current, intent):
    _validate_current(current)
    _validate_intent(intent)
    _require(intent["generation"] != current["generation"], "GENERATION_REPLAY")
    _require(intent["prior_receipt_sha256"] == _canonical_digest(current),
             "PRIOR_RECEIPT_CHANGED")
    _require(intent["daemon_id"] == current["daemon_id"], "DAEMON_CHANGED")


def _genesis_unsealed():
    # A legacy name, image, Docker inspect or preexisting intent must never be
    # promoted to a fabricated three-CID current receipt.
    _verify_root_directory()
    _require(not os.path.lexists(TRUSTED_ROOT / "current.json") and
             not os.path.lexists(TRUSTED_ROOT / "intent.json"),
             "GENESIS_HAS_PREDECESSOR")


def _genesis_intent(genesis):
    _validate_genesis(genesis)
    intent = {key: genesis[key] for key in _COMMON}
    intent["prior_receipt_sha256"] = _canonical_digest(genesis)
    _validate_intent(intent)
    return intent


def _birth_file_path(intent, role):
    _require(role in ROLES, "ROLE_UNKNOWN")
    return TRUSTED_ROOT / "generations" / intent["generation"] / (role + ".cid")


def _pending_file_path(intent):
    _require(type(intent) is dict and _is_token(intent.get("generation"), _HEX32),
             "PENDING_IDENTITY")
    return TRUSTED_ROOT / "generations" / intent["generation"] / "pending.json"


def _journal_file_path(intent):
    _require(type(intent) is dict and _is_token(intent.get("generation"), _HEX32),
             "JOURNAL_IDENTITY")
    return TRUSTED_ROOT / "generations" / intent["generation"] / "operations.json"


def _verify_private_dir(path):
    """Check a managed child directory; no auto-adoption of foreign paths."""
    try:
        meta = os.lstat(path)
    except OSError as exc:
        raise CustodyHold("PRIVATE_DIR_MISSING") from exc
    _require(stat.S_ISDIR(meta.st_mode) and meta.st_uid == ROOT_UID and
             meta.st_mode & 0o077 == 0, "PRIVATE_DIR_OWNER_OR_MODE")


def _read_birth_cidfile(intent, role):
    path = _birth_file_path(intent, role)
    _verify_private_dir(path.parent)
    raw = _read_regular_file(path, limit=80)
    try:
        cid = raw.decode("ascii").strip("\n")
    except UnicodeError as exc:
        raise CustodyHold("BIRTH_CIDFILE_INVALID") from exc
    _require(_is_token(cid, _CID) and raw in (cid.encode(), (cid + "\n").encode()),
             "BIRTH_CIDFILE_INVALID")
    return cid


def _read_pending(intent):
    """Read only this generation; never reuse or erase the prior receipt."""
    path = _pending_file_path(intent)
    _verify_root_directory()
    _verify_private_dir(TRUSTED_ROOT / "generations")
    if not os.path.lexists(path.parent):
        return None
    _verify_private_dir(path.parent)
    if not os.path.lexists(path):
        return None
    result = _strict_json(_read_regular_file(path))
    _validate_pending(result)
    return result


def _validate_pending(pending):
    _require(type(pending) is dict and set(pending) == {
        "generation", "intent_digest", "prepared_roles", "roles"}, "PENDING_SCHEMA")
    _require(_is_token(pending["generation"], _HEX32) and
             _is_token(pending["intent_digest"], _CID), "PENDING_IDENTITY")
    _require(type(pending["prepared_roles"]) is list and
             all(type(r) is str and r in ROLES for r in pending["prepared_roles"]) and
             len(set(pending["prepared_roles"])) == len(pending["prepared_roles"]),
             "PENDING_PREPARED_ROLES")
    _require(type(pending["roles"]) is dict and
             set(pending["roles"]).issubset(pending["prepared_roles"]),
             "PENDING_BIRTH_ROLES")


def _atomic_write_private(name, value, *, directory=None):
    """Bounded root-0600 same-directory replacement; never unlink an alias."""
    _verify_root_directory()
    _require(name in {"pending.json", "current.json", "operations.json"},
             "WRITE_TARGET")
    directory = TRUSTED_ROOT if directory is None else Path(directory)
    if directory != TRUSTED_ROOT:
        _require(directory.parent == TRUSTED_ROOT / "generations" and
                 _is_token(directory.name, _HEX32), "WRITE_DIRECTORY")
        _verify_private_dir(directory)
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     allow_nan=False).encode("utf-8")
    _require(0 < len(raw) <= MAX_RECEIPT_BYTES, "WRITE_BOUNDS")
    target = directory / name
    if os.path.lexists(target):
        _read_regular_file(target)
    temporary = directory / (".new-" + secrets.token_hex(16))
    flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL |
             getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        fd = os.open(temporary, flags, 0o600)
        try:
            _require(os.fstat(fd).st_uid == ROOT_UID, "WRITE_NOT_ROOT")
            os.write(fd, raw)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temporary, target)
        dirfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    except OSError as exc:
        # A failed write is not authority for a cleanup of unknown files.
        raise CustodyHold("PRIVATE_WRITE_FAILED") from exc


def _write_pending(pending):
    _validate_pending(pending)
    _atomic_write_private("pending.json", pending,
                          directory=TRUSTED_ROOT / "generations" / pending["generation"])


def _write_current(current):
    _validate_current(current)
    _atomic_write_private("current.json", current)


def _empty_journal(intent):
    return {"schema_version": 1, "generation": intent["generation"],
            "intent_digest": _canonical_digest(intent), "events": []}


def _validate_journal(intent, journal, *, expected_intent_digest=None):
    digest = (_canonical_digest(intent) if expected_intent_digest is None
              else expected_intent_digest)
    _require(_is_token(digest, _CID), "JOURNAL_INTENT_IDENTITY")
    _require(type(journal) is dict and set(journal) == {
        "schema_version", "generation", "intent_digest", "events"}, "JOURNAL_SCHEMA")
    _require(type(journal["schema_version"]) is int and
             journal["schema_version"] == 1 and
             journal["generation"] == intent["generation"] and
             journal["intent_digest"] == digest,
             "JOURNAL_ATTEMPT")
    events = journal["events"]
    _require(type(events) is list and len(events) <= 256, "JOURNAL_BOUNDS")
    active = {}
    seen = set()
    for event in events:
        _require(type(event) is dict and set(event) == {
            "id", "phase", "role", "operation", "cid", "timestamp"},
            "JOURNAL_EVENT_SCHEMA")
        _require(_is_token(event["id"], _HEX32) and
                 _is_token(event["cid"], _CID) and
                 event["role"] in ROLES and
                 event["operation"] in _JOURNAL_OPERATIONS and
                 event["phase"] in _JOURNAL_PHASES and
                 type(event["timestamp"]) is str and
                 12 <= len(event["timestamp"]) <= 40, "JOURNAL_EVENT_FORMAT")
        identifier = event["id"]
        if event["phase"] == "REQUESTED":
            _require(identifier not in seen, "JOURNAL_REPLAY")
            seen.add(identifier)
            active[identifier] = (event["role"], event["operation"], event["cid"])
        else:
            _require(active.pop(identifier, None) == (
                event["role"], event["operation"], event["cid"]),
                "JOURNAL_CONFIRMATION_MISMATCH")
    return active


def _load_journal(intent, *, expected_intent_digest=None):
    """A missing first journal is empty; an existing corrupt one always HOLDs."""
    path = _journal_file_path(intent)
    _verify_root_directory()
    _verify_private_dir(TRUSTED_ROOT / "generations")
    _verify_private_dir(path.parent)
    if not os.path.lexists(path):
        return _empty_journal(intent)
    journal = _strict_json(_read_regular_file(path))
    _validate_journal(intent, journal,
                      expected_intent_digest=expected_intent_digest)
    return journal


def _critical_start_confirmed(journal, cid):
    return any(e["phase"] == "CONFIRMED" and e["role"] == "critical" and
               e["operation"] == "start" and e["cid"] == cid
               for e in journal["events"])


def _verify_sealed_birth(current, role):
    """Require original Docker cidfile+pending and critical start journal.

    A root-owned `current.json` alone is NOT evidence that legacy containers
    were created through the trusted lifecycle. No retrospective name-only
    or label-only admission can pass this proof.
    """
    _require(role in ROLES, "ROLE_UNKNOWN")
    record = current["roles"][role]
    _require(_read_birth_cidfile(current, role) == record["cid"],
             "SEALED_CIDFILE_MISMATCH")
    pending = _read_pending(current)
    _require(type(pending) is dict and
             pending["generation"] == current["generation"] and
             pending["intent_digest"] == current["birth_intent_sha256"] and
             set(pending["prepared_roles"]) == set(ROLES) and
             pending["roles"] == current["roles"], "SEALED_BIRTH_PENDING_MISMATCH")
    if role == "critical":
        journal = _load_journal(
            current, expected_intent_digest=current["birth_intent_sha256"])
        _require(not _unresolved_events(journal) and
                 _critical_start_confirmed(journal, record["cid"]),
                 "SEALED_CRITICAL_START_UNPROVEN")


def _journal_event(intent, *, role, operation, cid, op_id, phase):
    """Durably persist request *before* Docker; confirmation after it returns."""
    _require(role in ROLES and operation in _JOURNAL_OPERATIONS and
             _is_token(cid, _CID) and _is_token(op_id, _HEX32) and
             phase in _JOURNAL_PHASES, "JOURNAL_EVENT_ARGUMENTS")
    journal = _load_journal(intent)
    unresolved = _validate_journal(intent, journal)
    if phase == "REQUESTED":
        _require(not unresolved, "PRIOR_OPERATION_UNRESOLVED")
    else:
        _require(unresolved.get(op_id) == (role, operation, cid),
                 "JOURNAL_CONFIRMATION_MISMATCH")
    journal["events"].append({
        "id": op_id, "phase": phase, "role": role, "operation": operation,
        "cid": cid, "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    _validate_journal(intent, journal)
    _atomic_write_private("operations.json", journal,
                          directory=_journal_file_path(intent).parent)


def _pending_for_intent(intent, pending):
    if pending is None:
        return {"generation": intent["generation"],
                "intent_digest": _canonical_digest(intent),
                "prepared_roles": [], "roles": {}}
    _validate_pending(pending)
    _require(pending["generation"] == intent["generation"] and
             pending["intent_digest"] == _canonical_digest(intent),
             "PENDING_OTHER_ATTEMPT")
    return pending


def _docker_run(argv, *, capture=False):
    _require(type(argv) in (tuple, list) and all(type(arg) is str for arg in argv),
             "DOCKER_ARGUMENTS")
    # The observed RC6 host uses a rootful daemon. A caller's Docker context,
    # PATH or DOCKER_HOST must never redirect a trusted CID to Boca's rootless
    # socket or any other daemon; invoking this requires the existing sudo.
    _require(len(argv) >= 2 and argv[0] == "docker", "DOCKER_SUBCOMMAND")
    command = (DOCKER_BINARY, "--host", DOCKER_ENDPOINT, *argv[1:])
    environment = {"HOME": "/root", "PATH": "/usr/bin:/bin", "LC_ALL": "C",
                   "DOCKER_CONFIG": "/root/.docker", "DOCKER_CLI_HINTS": "false"}
    try:
        return subprocess.run(command, check=True, text=True, timeout=20,
                              stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, env=environment)
    except (subprocess.SubprocessError, OSError) as exc:
        raise CustodyHold("DOCKER_EXECUTION_UNVERIFIED") from exc


def _daemon_identity():
    raw = _docker_run(("docker", "info", "--format", "{{.ID}}"), capture=True).stdout
    _require(type(raw) is str and 6 <= len(raw.strip()) <= 128,
             "DAEMON_ID_UNVERIFIED")
    return raw.strip()


def _inspect_exact_cid(cid):
    _require(_is_token(cid, _CID), "BIRTH_CID")
    raw = _docker_run(("docker", "inspect", "--type", "container", cid),
                      capture=True).stdout
    _require(type(raw) is str and len(raw) <= 262144, "INSPECT_BOUNDS")
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise CustodyHold("INSPECT_JSON") from exc
    _require(type(obj) is list and len(obj) == 1 and type(obj[0]) is dict,
             "INSPECT_SHAPE")
    return obj[0]


def _verify_inspect(role, record, expected, observed):
    name = _birth_name(role, expected["generation"])
    _require(record.get("name") == name and
             type(observed) is dict and observed.get("Id") == record["cid"] and
             observed.get("Image") == record["image_id"] and
             observed.get("Name") == record["name"] and
             observed.get("Created") == record["created"], "INSPECT_BIRTH_MISMATCH")
    _require((observed.get("Config") or {}).get("Labels") is not None,
             "INSPECT_LABELS")
    labels = observed["Config"]["Labels"]
    _require(type(labels) is dict and
             labels.get("porota.rc6.role") == role and
             labels.get("porota.rc6.generation") == expected["generation"] and
             labels.get("porota.rc6.source_sha") == expected["candidate_sha"],
             "INSPECT_PROVENANCE")
    _require(labels.get("porota.rc6.birth_name") == name[1:],
             "INSPECT_BIRTH_NAME_PROVENANCE")
    _require(type(observed.get("State")) is dict and
             observed["State"].get("Status") in ("running", "exited", "created"),
             "INSPECT_STATE")


def _attest_critical_exclusivity(intent, observed):
    """Require an external, fresh, exact-CID proof *before* critical start.

    No Docker, systemd or legacy mutation occurs here. A root-owned attestor
    outside candidate code must first establish stopped legacy ownership and
    absence of concurrent Telegram consumers. An assertion without that
    independent host authority is no authority and must not be provisioned.
    """
    path = TRUSTED_ROOT / "generations" / intent["generation"] / "critical_exclusivity.json"
    _verify_root_directory()
    _verify_private_dir(path.parent)
    proof = _strict_json(_read_regular_file(path))
    _require(set(proof) == _CRITICAL_EXCLUSIVE_KEYS and
             type(proof["schema_version"]) is int and proof["schema_version"] == 1 and
             proof["state"] == "CRITICAL_EXCLUSIVE" and
             proof["authority"] == "INDEPENDENT_HOST_CONTROL_PLANE" and
             all(proof.get(key) == intent[key] for key in (
                 "generation", "candidate_sha", "run_id", "run_attempt",
                 "daemon_id", "image_id")) and
             proof["newborn_cid"] == observed["Id"] and
             proof["newborn_created"] == observed["Created"] and
             proof["legacy_critical_quiesced"] is True and
             proof["systemd_unit_quiesced"] is True and
             proof["single_consumer_exclusive"] is True,
             "CRITICAL_EXCLUSIVITY_UNPROVEN")
    try:
        created = datetime.fromisoformat(observed["Created"].replace("Z", "+00:00"))
        attested = datetime.fromisoformat(proof["attested_at"].replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
    except (AttributeError, TypeError, ValueError) as exc:
        raise CustodyHold("CRITICAL_ATTESTATION_TIME_INVALID") from exc
    _require(created.tzinfo is not None and attested.tzinfo is not None and
             created <= attested <= now + timedelta(seconds=5) and
             timedelta(0) <= now - attested <= timedelta(seconds=90),
             "CRITICAL_ATTESTATION_STALE")


_OBSERVER_HASH_PROFILES = {
    ("/usr/bin/sha256sum", "/app/cf_intraday_scalping.py"),
    ("/usr/bin/sha256sum", "/app/co_market_sessions_hf6.py"),
}


def _operation_argv(operation, cid, args, *, role):
    _require(_is_token(cid, _CID), "BIRTH_CID")
    _require(all(type(a) is str and len(a) < 4096 for a in args), "OP_ARGUMENTS")
    if operation == "stop" and not args:
        return ("docker", "stop", "--time", "10", cid)
    if operation == "remove" and not args:
        return ("docker", "rm", cid)
    if operation == "start" and not args:
        return ("docker", "start", cid)
    if operation == "logs" and not args:
        return ("docker", "logs", "--tail", "80", cid)
    if operation == "exec" and role == "observer" and tuple(args) in _OBSERVER_HASH_PROFILES:
        return ("docker", "exec", cid, *args)
    raise CustodyHold("OPERATION_UNSUPPORTED")


def _unresolved_events(journal):
    _require(type(journal) is dict and type(journal.get("events")) is list,
             "JOURNAL_SCHEMA")
    unresolved = {}
    for event in journal["events"]:
        _require(type(event) is dict and
                 event.get("phase") in _JOURNAL_PHASES and
                 _is_token(event.get("id"), _HEX32), "JOURNAL_EVENT_FORMAT")
        identifier = event["id"]
        if event["phase"] == "REQUESTED":
            _require(identifier not in unresolved, "JOURNAL_REPLAY")
            unresolved[identifier] = event
        else:
            _require(identifier in unresolved, "JOURNAL_CONFIRMATION_MISMATCH")
            del unresolved[identifier]
    return unresolved


class CustodySession:
    def __init__(self, current, intent, *, genesis=False):
        self._genesis = genesis
        self._prior_digest = _canonical_digest(current)
        self._generation = intent["generation"]

    def _refresh(self):
        if self._genesis:
            _genesis_unsealed()
            genesis = _read_trusted_genesis()
            intent = _genesis_intent(genesis)
            _require(_canonical_digest(genesis) == self._prior_digest and
                     intent["generation"] == self._generation, "GENESIS_CHANGED")
            return None, intent
        current = _read_trusted_current()
        intent = _read_trusted_intent()
        _assert_continuity(current, intent)
        _require(_canonical_digest(current) == self._prior_digest and
                 intent["generation"] == self._generation, "ATTEMPT_CHANGED")
        return current, intent

    def verify_role(self, role, expected_context=None):
        """Read-only exact CID proof, not permission for a later name operation."""
        _require(not self._genesis, "LEGACY_UNTRUSTED_NO_CUSTODY")
        _require(role in ROLES, "ROLE_UNKNOWN")
        current, intent = self._refresh()
        _require(expected_context is None or expected_context == intent,
                 "EXPECTED_CONTEXT")
        _verify_sealed_birth(current, role)
        _require(_daemon_identity() == intent["daemon_id"], "DAEMON_CHANGED")
        record = current["roles"][role]
        observed = _inspect_exact_cid(record["cid"])
        _verify_inspect(role, record, current, observed)
        return observed

    def birth_labels(self, role):
        """Return only attempt-attested provenance flags for a trusted creator."""
        _require(role in ROLES, "ROLE_UNKNOWN")
        _, intent = self._refresh()
        labels = {
            "porota.rc6.role": role,
            "porota.rc6.generation": intent["generation"],
            "porota.rc6.source_sha": intent["candidate_sha"],
            "porota.rc6.run_id": intent["run_id"],
            "porota.rc6.run_attempt": intent["run_attempt"],
            "porota.rc6.artifact_digest": intent["artifact_digest"],
            "porota.rc6.birth_name": _birth_name(role, intent["generation"])[1:],
        }
        return tuple(token for key, value in labels.items()
                     for token in ("--label", key + "=" + value))

    def birth_name(self, role):
        """Return the trusted, collision-free CLI --name for a NEW birth."""
        _, intent = self._refresh()
        return _birth_name(role, intent["generation"])[1:]

    def _verify_pending_locked(self, role, *, require_running=True):
        _require(role in ROLES, "ROLE_UNKNOWN")
        _, intent = self._refresh()
        pending = _pending_for_intent(intent, _read_pending(intent))
        _require(role in pending["roles"], "BIRTH_NOT_RECORDED")
        record = pending["roles"][role]
        _require(type(record) is dict and set(record) == _RECORD_KEYS and
                 _is_token(record["cid"], _CID), "BIRTH_RECORD_SCHEMA")
        _require(_read_birth_cidfile(intent, role) == record["cid"],
                 "BIRTH_CIDFILE_MISMATCH")
        _require(_daemon_identity() == intent["daemon_id"], "DAEMON_CHANGED")
        observed = _inspect_exact_cid(record["cid"])
        _verify_inspect(role, record, intent, observed)
        if require_running:
            _require(observed["State"]["Status"] == "running",
                     "PENDING_BIRTH_NOT_RUNNING")
        return record["cid"], observed, intent

    def verify_pending_birth(self, role):
        """Read-only verification of a newly born CID, never the old current CID."""
        with _root_lock():
            _, observed, _ = self._verify_pending_locked(role)
            return observed

    def _execute_verified(self, role, operation, cid, observed, intent, argv,
                          *, capture, allow_created_start=False):
        state = observed["State"]["Status"]
        if operation == "remove":
            _require(state == "exited", "REMOVE_RUNNING_HOLD")
        if operation == "stop":
            _require(state == "running", "STOP_NONRUNNING_HOLD")
        if operation == "start":
            _require(state == ("created" if allow_created_start else "exited"),
                     "START_NONEXITED_HOLD")
        cmd = _operation_argv(operation, cid, argv, role=role)
        if operation == "logs":
            return _docker_run(cmd, capture=capture)
        _require(not _unresolved_events(_load_journal(intent)),
                 "PRIOR_OPERATION_UNRESOLVED")
        op_id = secrets.token_hex(16)
        _journal_event(intent, role=role, operation=operation, cid=cid,
                       op_id=op_id, phase="REQUESTED")
        result = _docker_run(cmd, capture=capture)
        if operation in ("stop", "start"):
            # Docker CLI returning zero is not proof of the desired state.
            # Keep REQUESTED unresolved on a mismatched or unobservable result.
            _require(_daemon_identity() == intent["daemon_id"], "DAEMON_CHANGED")
            after = _inspect_exact_cid(cid)
            _require(type(after) is dict and after.get("Id") == cid and
                     after.get("Image") == observed.get("Image") and
                     after.get("Created") == observed.get("Created") and
                     after.get("Name") == observed.get("Name") and
                     (after.get("Config") or {}).get("Labels") ==
                     (observed.get("Config") or {}).get("Labels"),
                     "POST_RUNTIME_IDENTITY_CHANGED")
            _require(type(after.get("State")) is dict and
                     after["State"].get("Status") == (
                         "exited" if operation == "stop" else "running"),
                     "POST_RUNTIME_STATE_UNVERIFIED")
        _journal_event(intent, role=role, operation=operation, cid=cid,
                       op_id=op_id, phase="CONFIRMED")
        return result

    def authorize_operation(self, role, operation, *argv, capture=False):
        """Re-verify under lock and EXECUTE by full immutable CID only."""
        _require(not self._genesis, "LEGACY_UNTRUSTED_NO_CUSTODY")
        _require(role in ROLES, "ROLE_UNKNOWN")
        # A prior birth's exclusivity proof does not authorize a *new* critical
        # consumer activation. Only authorize_newborn_start has fresh external
        # fencing; never restart a sealed critical via the generic interface.
        _require(not (role == "critical" and operation == "start"),
                 "SEALED_CRITICAL_RESTART_REQUIRES_FRESH_FENCING")
        with _root_lock():
            current, intent = self._refresh()
            observed = self.verify_role(role)
            cid = current["roles"][role]["cid"]
            return self._execute_verified(role, operation, cid, observed,
                                          intent, argv, capture=capture)

    def authorize_newborn_operation(self, role, operation, *argv, capture=False):
        """Permit only a narrowly profiled, journaled command on a new birth."""
        _require(operation == "exec", "NEWBORN_OPERATION_UNSUPPORTED")
        with _root_lock():
            cid, observed, intent = self._verify_pending_locked(role)
            return self._execute_verified(role, operation, cid, observed,
                                          intent, argv, capture=capture)

    def authorize_newborn_start(self, role):
        """Start only a recorded CREATED birth using its immutable CID."""
        with _root_lock():
            cid, observed, intent = self._verify_pending_locked(
                role, require_running=False)
            _require(observed["State"]["Status"] == "created",
                     "NEWBORN_NOT_CREATED")
            if role == "critical":
                _attest_critical_exclusivity(intent, observed)
            return self._execute_verified(role, "start", cid, observed, intent,
                                          (), capture=False, allow_created_start=True)

    def reconcile_operation(self, op_id):
        """Resolve only a proven stop/start postcondition, without retrying Docker.

        An arbitrary exec, removal, missing CID, or daemon ambiguity remains
        REQUESTED. A new deploy must not auto-replay an uncertain side effect.
        """
        _require(_is_token(op_id, _HEX32), "OPERATION_ID")
        with _root_lock():
            current, intent = self._refresh()
            unresolved = _unresolved_events(_load_journal(intent))
            _require(set(unresolved) == {op_id}, "OPERATION_NOT_UNIQUELY_PENDING")
            event = unresolved[op_id]
            role, operation, cid = (event["role"], event["operation"], event["cid"])
            _require(role in ROLES and operation in ("stop", "start") and
                     _is_token(cid, _CID), "RECONCILIATION_UNSUPPORTED")
            pending = (_pending_for_intent(intent, _read_pending(intent))
                       if operation == "start" else None)
            newborn = (pending is not None and role in pending["roles"] and
                       pending["roles"][role]["cid"] == cid)
            # Historical REQUESTED is a crash marker, not a substitute for
            # independent, current proof of critical consumer exclusivity.
            _require(not (role == "critical" and operation == "start" and
                          not newborn),
                     "SEALED_CRITICAL_RESTART_RECONCILIATION_DENIED")
            if newborn:
                _require(_read_birth_cidfile(intent, role) == cid,
                         "RECONCILIATION_CIDFILE_CHANGED")
                record, expected = pending["roles"][role], intent
            else:
                _require(not self._genesis, "LEGACY_UNTRUSTED_NO_CUSTODY")
                record, expected = current["roles"][role], current
                _require(cid == record["cid"], "RECONCILIATION_CID_CHANGED")
            _require(_daemon_identity() == intent["daemon_id"], "DAEMON_CHANGED")
            observed = _inspect_exact_cid(cid)
            _verify_inspect(role, record, expected, observed)
            _require(observed["State"]["Status"] == (
                "exited" if operation == "stop" else "running"),
                "RECONCILIATION_POSTSTATE_UNVERIFIED")
            if newborn and role == "critical":
                _attest_critical_exclusivity(intent, observed)
            _journal_event(intent, role=role, operation=operation, cid=cid,
                           op_id=op_id, phase="CONFIRMED")
            return {"status": "RECONCILED", "role": role,
                    "operation": operation, "generation": intent["generation"]}

    def prepare_birth(self, role):
        """Reserve one fresh cidfile path; Docker itself must create the file."""
        _require(role in ROLES, "ROLE_UNKNOWN")
        with _root_lock():
            _, intent = self._refresh()
            path = _birth_file_path(intent, role)
            _require(not os.path.lexists(path), "BIRTH_CIDFILE_ALREADY_EXISTS")
            _verify_private_dir(TRUSTED_ROOT / "generations")
            if not os.path.lexists(path.parent):
                try:
                    os.mkdir(path.parent, 0o700)
                except OSError as exc:
                    raise CustodyHold("BIRTH_DIR_CREATE_FAILED") from exc
            _verify_private_dir(path.parent)
            pending = _pending_for_intent(intent, _read_pending(intent))
            _require(role not in pending["prepared_roles"] and
                     role not in pending["roles"], "BIRTH_ALREADY_PREPARED")
            pending["prepared_roles"].append(role)
            _write_pending(pending)
            return path

    def record_birth(self, role, created_cid):
        """Correlate the fresh CLI birth ID with an exclusive cidfile and inspect."""
        _require(role in ROLES and _is_token(created_cid, _CID), "BIRTH_CID")
        with _root_lock():
            _, intent = self._refresh()
            pending = _pending_for_intent(intent, _read_pending(intent))
            _require(role in pending["prepared_roles"] and
                     role not in pending["roles"], "BIRTH_NOT_PREPARED")
            _require(_read_birth_cidfile(intent, role) == created_cid,
                     "BIRTH_CIDFILE_MISMATCH")
            _require(_daemon_identity() == intent["daemon_id"], "DAEMON_CHANGED")
            observed = _inspect_exact_cid(created_cid)
            record = {"cid": created_cid, "image_id": intent["image_id"],
                      "created": observed.get("Created"),
                      "name": _birth_name(role, intent["generation"])}
            _verify_inspect(role, record, intent, observed)
            if role == "critical":
                _require(observed["State"]["Status"] == "created",
                         "CRITICAL_MUST_START_UNDER_EXCLUSIVITY")
            labels = observed["Config"]["Labels"]
            _require(labels.get("porota.rc6.run_id") == intent["run_id"] and
                     labels.get("porota.rc6.run_attempt") == intent["run_attempt"] and
                     labels.get("porota.rc6.artifact_digest") == intent["artifact_digest"],
                     "BIRTH_ATTEMPT_MISMATCH")
            _require(created_cid not in {r["cid"] for r in pending["roles"].values()} and
                     (self._genesis or created_cid not in {
                         r["cid"] for r in _read_trusted_current()["roles"].values()}),
                     "BIRTH_REUSED_CID")
            pending["roles"][role] = record
            _write_pending(pending)

    def seal_generation(self):
        """Promote only an exact complete, independently observed new 3-role set."""
        with _root_lock():
            current, intent = self._refresh()
            pending = _pending_for_intent(intent, _read_pending(intent))
            _require(set(pending["prepared_roles"]) == set(ROLES) and
                     set(pending["roles"]) == set(ROLES), "THREE_NEW_BIRTHS_REQUIRED")
            journal = _load_journal(intent)
            _require(not _unresolved_events(journal),
                     "PRIOR_OPERATION_UNRESOLVED")
            critical_cid = pending["roles"]["critical"]["cid"]
            _require(_critical_start_confirmed(journal, critical_cid),
                     "CRITICAL_START_NOT_ATTESTED")
            _require(_daemon_identity() == intent["daemon_id"], "DAEMON_CHANGED")
            result = {key: intent[key] for key in _COMMON}
            result.update({"sealed": True, "roles": pending["roles"],
                           "birth_intent_sha256": _canonical_digest(intent)})
            _validate_current(result)
            for role in ROLES:
                record = result["roles"][role]
                observed = _inspect_exact_cid(record["cid"])
                _verify_inspect(role, record, intent, observed)
                _require(observed["State"]["Status"] == "running",
                         "SEAL_ROLE_NOT_RUNNING")
                if role == "critical":
                    try:
                        _attest_critical_exclusivity(intent, observed)
                    except CustodyHold as exc:
                        raise CustodyHold("CRITICAL_EXCLUSIVITY_UNPROVEN") from exc
            if self._genesis:
                _genesis_unsealed()
                _require(_canonical_digest(_read_trusted_genesis()) ==
                         self._prior_digest, "GENESIS_CHANGED")
            else:
                _require(_canonical_digest(current) == intent["prior_receipt_sha256"],
                         "PREDECESSOR_CHANGED")
            _write_current(result)
            return {"schema_version": 1, "sealed": True,
                    "role_count": 3, "generation": intent["generation"]}


def begin_attempt(trusted_intent=None):
    """Bind to root-owned current+intent; never use an env-controlled bypass."""
    _require(trusted_intent is None, "INTENT_CALLER_OVERRIDE_FORBIDDEN")
    current = _read_trusted_current()
    intent = _read_trusted_intent()
    _assert_continuity(current, intent)
    return CustodySession(current, intent)


def begin_genesis():
    """One first birth attempt, only with external root-owned LEGACY_UNTRUSTED.

    No code path in this mode may stop, remove, inspect-as-owned, or adopt a
    legacy container. The external authority must provision the private root,
    `genesis.json`, and lock, then separately attest critical exclusivity.
    """
    with _root_lock():
        _genesis_unsealed()
        genesis = _read_trusted_genesis()
        intent = _genesis_intent(genesis)
        return CustodySession(genesis, intent, genesis=True)
