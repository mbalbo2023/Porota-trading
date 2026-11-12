"""R280 offline contract for a future *independently bootstrapped* installer.

This module is source code, NOT permission to run it with sudo from a GitHub
checkout. Its installation entry has no CLI, a missing external manifest pin,
and a hard fail-closed root-installed path check. There is no workflow or host
activation here. A separately audited root-owned launcher and bootstrap
authority must exist before physical installation can be considered.

Exactly four approved Porota-only files may be installed. The approval is
published LAST, after each prior target has been atomically renamed and
fsynced. A crash can leave a partial code set; existing custody must then
HOLD79 on mismatching approved hashes. No rollback, Docker, systemd, database,
PPI Watch or Boca interaction occurs in this module.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat


class InstallHold(RuntimeError):
    exit_code = 79

    def __init__(self, reason):
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,70}", reason):
            reason = "UNVERIFIED"
        self.reason = reason
        super().__init__("RC6_ROOT_INSTALL_HOLD79:" + reason)


def _require(ok, reason):
    if not ok:
        raise InstallHold(reason)


_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_HEX32 = re.compile(r"[0-9a-f]{32}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_DECIMAL = re.compile(r"[1-9][0-9]{0,19}\Z")
_DAEMON = re.compile(r"[A-Za-z0-9:_./-]{6,128}\Z")
_MANIFEST_KEYS = frozenset({
    "schema", "candidate_sha", "candidate_tree", "paper_mode",
    "real_orders_allowed", "files",
})
_APPROVAL_KEYS = frozenset({
    "schema_version", "phase", "candidate_sha", "candidate_tree",
    "artifact_digest", "image_id", "daemon_id", "generation", "run_id",
    "run_attempt", "paper_mode", "real_orders_allowed", "helper_sha256",
    "custody_sha256", "profiles_sha256", "unit_template_sha256", "expires_at",
})

# A trusted-main review/host installer must independently pin THIS digest,
# never derive it from the bundle itself or from workflow_dispatch input.
# None is intentional and blocks all real installation.
TRUSTED_MANIFEST_SHA256 = None
INSTALLED_CONTRACT_PATH = (
    "/usr/local/libexec/porota-rc6-custody/rc6_host_rootowned_installer_contract.py"
)
ROOT_UID = 0
NOFOLLOW_FLAG = getattr(os, "O_NOFOLLOW", None)
INBOX_DIR = "/var/lib/porota-rc6-oci-custody/install-inbox"
SOURCE_NAMES = {
    "helper": "helper.bin",
    "custody": "custody.py",
    "profiles": "profiles.json",
    "approval": "approval.json",
}
TARGETS = {
    "helper": ("/usr/local/sbin/porota-rc6-oci-custody", 0o555, 2 * 1024 * 1024),
    "custody": (
        "/usr/local/libexec/porota-rc6-custody/rc6_runtime_oci_custody.py",
        0o400, 8 * 1024 * 1024,
    ),
    "profiles": (
        "/var/lib/porota-rc6-oci-custody/host-launch-profiles.json",
        0o400, 128 * 1024,
    ),
    "approval": (
        "/var/lib/porota-rc6-oci-custody/host-helper-approval.json",
        0o400, 65536,
    ),
}
ORDER = ("helper", "custody", "profiles", "approval")
_TARGET_BASENAMES = frozenset(Path(value[0]).name for value in TARGETS.values())
_TARGET_MODES = {Path(value[0]).name: value[1] for value in TARGETS.values()}


@dataclass(frozen=True)
class VerifiedBundle:
    candidate_sha: str
    manifest_sha256: str
    files: dict[str, bytes]
    host_control_verified: bool = False


def _strict_json(raw, *, limit):
    _require(type(raw) is bytes and 0 < len(raw) <= limit, "ROOT_JSON_SIZE")

    def unique_pairs(pairs):
        doc = {}
        for key, value in pairs:
            _require(key not in doc, "ROOT_JSON_DUPLICATE_KEY")
            doc[key] = value
        return doc

    def forbid_nonfinite(_value):
        raise InstallHold("ROOT_JSON_NONFINITE")

    try:
        doc = json.loads(raw.decode("utf-8", "strict"),
                         object_pairs_hook=unique_pairs,
                         parse_constant=forbid_nonfinite)
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise InstallHold("ROOT_JSON_INVALID") from exc
    _require(type(doc) is dict, "ROOT_JSON_NOT_OBJECT")
    return doc


def _validate_approval(doc, manifest, sources, *, now):
    _require(set(doc) == _APPROVAL_KEYS and
             type(doc["schema_version"]) is int and doc["schema_version"] == 1 and
             doc["phase"] in ("GENESIS", "CONTINUITY"), "ROOT_APPROVAL_SCHEMA")
    _require(doc["candidate_sha"] == manifest["candidate_sha"] and
             doc["candidate_tree"] == manifest["candidate_tree"] and
             doc["paper_mode"] == "PRODUCTION_PAPER" and
             doc["real_orders_allowed"] is False, "ROOT_APPROVAL_SCOPE")
    _require(all(type(doc[key]) is str and _HEX64.fullmatch(doc[key]) for key in
                 ("helper_sha256", "custody_sha256", "profiles_sha256",
                  "unit_template_sha256")), "ROOT_APPROVAL_HASH")
    _require(all(doc[role + "_sha256"] == hashlib.sha256(sources[role]).hexdigest()
                 for role in ("helper", "custody", "profiles")),
             "ROOT_APPROVAL_SOURCE_MISMATCH")
    _require(all(type(doc[key]) is str and _DIGEST.fullmatch(doc[key]) for key in
                 ("image_id", "artifact_digest")) and
             type(doc["daemon_id"]) is str and _DAEMON.fullmatch(doc["daemon_id"]) and
             type(doc["generation"]) is str and _HEX32.fullmatch(doc["generation"]) and
             all(type(doc[key]) is str and _DECIMAL.fullmatch(doc[key]) for key in
                 ("run_id", "run_attempt")), "ROOT_APPROVAL_INTENT")
    _require(type(now) is datetime and now.tzinfo is not None,
             "ROOT_APPROVAL_CLOCK")
    expires_at = doc["expires_at"]
    _require(type(expires_at) is str and
             re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", expires_at),
             "ROOT_APPROVAL_EXPIRY")
    try:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InstallHold("ROOT_APPROVAL_EXPIRY") from exc
    _require(now < expiry <= now + timedelta(hours=4), "ROOT_APPROVAL_EXPIRED")


def verify_approved_bundle(manifest_raw, source_bytes, *, now):
    """Pure verification; source bytes are never authorization of their own hash."""
    pin = TRUSTED_MANIFEST_SHA256
    _require(type(pin) is str and _HEX64.fullmatch(pin),
             "ROOT_MANIFEST_TRUST_PIN_ABSENT")
    manifest = _strict_json(manifest_raw, limit=8192)
    manifest_digest = hashlib.sha256(manifest_raw).hexdigest()
    _require(manifest_digest == pin, "ROOT_MANIFEST_TRUST_PIN_MISMATCH")
    _require(set(manifest) == _MANIFEST_KEYS and
             manifest["schema"] == "RC6_ROOT_INSTALL_MANIFEST_V1" and
             type(manifest["candidate_sha"]) is str and
             _HEX40.fullmatch(manifest["candidate_sha"]) and
             type(manifest["candidate_tree"]) is str and
             _HEX40.fullmatch(manifest["candidate_tree"]) and
             manifest["paper_mode"] == "PRODUCTION_PAPER" and
             manifest["real_orders_allowed"] is False and
             type(manifest["files"]) is dict and
             set(manifest["files"]) == set(ORDER), "ROOT_MANIFEST_SCHEMA")
    _require(type(source_bytes) is dict and set(source_bytes) == set(ORDER),
             "ROOT_BUNDLE_ROLE_SCOPE")
    for role in ORDER:
        expected_sha = manifest["files"][role]
        data = source_bytes[role]
        _require(type(expected_sha) is str and _HEX64.fullmatch(expected_sha) and
                 type(data) is bytes and 0 < len(data) <= TARGETS[role][2],
                 "ROOT_BUNDLE_ROLE_SCHEMA")
        _require(hashlib.sha256(data).hexdigest() == expected_sha,
                 "ROOT_BUNDLE_HASH_MISMATCH")
    _require(source_bytes["helper"].startswith(b"#!/usr/bin/python3 -I\n"),
             "ROOT_HELPER_ISOLATED_PYTHON_REQUIRED")
    approval = _strict_json(source_bytes["approval"], limit=65536)
    _validate_approval(approval, manifest, source_bytes, now=now)
    return VerifiedBundle(candidate_sha=manifest["candidate_sha"],
                          manifest_sha256=manifest_digest,
                          files=dict(source_bytes))


def _protected_inode(inode, *, is_dir):
    if is_dir:
        _require(stat.S_ISDIR(inode.st_mode), "ROOT_INODE_KIND")
    else:
        _require(stat.S_ISREG(inode.st_mode) and inode.st_nlink == 1,
                 "ROOT_INODE_ALIAS")
    _require(inode.st_uid == ROOT_UID and not inode.st_mode & 0o022,
             "ROOT_INODE_OWNER_OR_MODE")


def _require_nofollow():
    _require(type(NOFOLLOW_FLAG) is int and NOFOLLOW_FLAG != 0,
             "ROOT_OS_NOFOLLOW_REQUIRED")


def _open_root_directory(path):
    """Walk every ancestor using O_NOFOLLOW; fixed paths only, never argv."""
    _require(type(path) is str and path.startswith("/") and
             "//" not in path and "/../" not in path and not path.endswith("/.."),
             "ROOT_FIXED_PATH")
    _require_nofollow()
    flags = os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW_FLAG
    fd = os.open("/", flags)
    try:
        _protected_inode(os.fstat(fd), is_dir=True)
        for component in Path(path).parts[1:]:
            _require(component not in ("", ".", ".."), "ROOT_FIXED_PATH")
            new_fd = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = new_fd
            _protected_inode(os.fstat(fd), is_dir=True)
        return fd
    except (OSError, InstallHold) as exc:
        os.close(fd)
        if isinstance(exc, InstallHold):
            raise
        raise InstallHold("ROOT_DIRECTORY_UNVERIFIED") from exc


def _read_root_inbox_file(dir_fd, name, max_bytes):
    _require(name in set(SOURCE_NAMES.values()) | {"manifest.json"},
             "ROOT_INBOX_FILE_NOT_ALLOWED")
    _require_nofollow()
    flags = os.O_RDONLY | NOFOLLOW_FLAG | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(name, flags, dir_fd=dir_fd)
        try:
            inode = os.fstat(fd)
            _protected_inode(inode, is_dir=False)
            _require(0 < inode.st_size <= max_bytes, "ROOT_BUNDLE_SOURCE_SIZE")
            content = os.read(fd, max_bytes + 1)
            after = os.fstat(fd)
            _require(len(content) == inode.st_size and
                     after.st_ino == inode.st_ino and after.st_size == inode.st_size,
                     "ROOT_BUNDLE_SOURCE_CHANGED")
            return content
        finally:
            os.close(fd)
    except OSError as exc:
        raise InstallHold("ROOT_BUNDLE_SOURCE_MISSING_OR_ALIAS") from exc


def atomic_publish(directory_fd, basename, raw, mode):
    """Atomic single-file publish; caller already holds validated dirfd.

    Approval-last ordering belongs to install_approved_bundle, not to a
    freeform CLI. Temp-only cleanup on failure is safe and is not a rollback.
    """
    _require(type(directory_fd) is int and directory_fd >= 0 and
             basename in _TARGET_BASENAMES and type(raw) is bytes and raw and
             mode == _TARGET_MODES.get(basename), "ROOT_ATOMIC_INSTALL_SHAPE")
    _require_nofollow()
    try:
        inode = os.stat(basename, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        inode = None
    except OSError as exc:
        raise InstallHold("ROOT_TARGET_UNVERIFIED") from exc
    if inode is not None:
        _protected_inode(inode, is_dir=False)
    temp_name = ".rc6-install-" + secrets.token_hex(16)
    flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL |
             NOFOLLOW_FLAG | getattr(os, "O_CLOEXEC", 0))
    created = False
    try:
        fd = os.open(temp_name, flags, mode, dir_fd=directory_fd)
        created = True
        try:
            os.fchmod(fd, mode)
            # os.open's mode is affected by umask; explicit fchmod gives exact
            # root-approved permission regardless of caller's environment.
            if os.geteuid() == 0:
                os.fchown(fd, ROOT_UID, 0)
            start = 0
            while start < len(raw):
                count = os.write(fd, raw[start:])
                _require(count > 0, "ROOT_ATOMIC_SHORT_WRITE")
                start += count
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temp_name, basename, src_dir_fd=directory_fd,
                   dst_dir_fd=directory_fd)
        created = False
        os.fsync(directory_fd)
    except (OSError, InstallHold) as exc:
        if created:
            try:
                os.unlink(temp_name, dir_fd=directory_fd)
            except OSError:
                pass
        if isinstance(exc, InstallHold):
            raise
        raise InstallHold("ROOT_ATOMIC_INSTALL_IO_UNVERIFIED") from exc


def publish_verified_bundle(verified):
    """Fixed, sequential root-owned file publication with approval LAST.

    Individual files are atomic. If a later path fails, earlier replacements
    remain (fix-forward); the absence/old hash of approval must make custody
    reject use until all files match independently approved intent.
    """
    _require(type(verified) is VerifiedBundle and
             set(verified.files) == set(ORDER) and
             verified.host_control_verified is False,
             "ROOT_VERIFIED_BUNDLE_REQUIRED")
    for role in ORDER:
        target, mode, _max_bytes = TARGETS[role]
        target_dir_fd = _open_root_directory(str(Path(target).parent))
        try:
            atomic_publish(target_dir_fd, Path(target).name,
                           verified.files[role], mode)
        finally:
            os.close(target_dir_fd)


def install_approved_bundle():
    """Physical entry inaccessible to normal checkout/CI; still HOLD by default.

    No caller paths, shell commands, mutable env selectors or host discovery.
    External root ownership/installer pin/independent prebirth are still P0.
    """
    _require(type(TRUSTED_MANIFEST_SHA256) is str and
             _HEX64.fullmatch(TRUSTED_MANIFEST_SHA256),
             "ROOT_MANIFEST_TRUST_PIN_ABSENT")
    _require(os.geteuid() == 0, "ROOT_INSTALL_EUID_REQUIRED")
    _require(str(Path(__file__).absolute()) == INSTALLED_CONTRACT_PATH,
             "ROOT_INSTALLED_BOOTSTRAP_UNVERIFIED")
    # Self-path must be immutable root-owned before this module is trusted;
    # the independent launcher also has to validate its own binary hash.
    source_dir = _open_root_directory(str(Path(INSTALLED_CONTRACT_PATH).parent))
    try:
        inode = os.stat(Path(INSTALLED_CONTRACT_PATH).name,
                        dir_fd=source_dir, follow_symlinks=False)
        _protected_inode(inode, is_dir=False)
    finally:
        os.close(source_dir)
    inbox_fd = _open_root_directory(INBOX_DIR)
    try:
        manifest = _read_root_inbox_file(inbox_fd, "manifest.json", 8192)
        sources = {role: _read_root_inbox_file(inbox_fd, SOURCE_NAMES[role],
                                             TARGETS[role][2]) for role in ORDER}
    finally:
        os.close(inbox_fd)
    verified = verify_approved_bundle(manifest, sources, now=datetime.now(timezone.utc))
    publish_verified_bundle(verified)
    return {"schema": "RC6_ROOT_INSTALL_CODE_ONLY_V1",
            "status": "INSTALLED_FILES_ONLY_NOT_HOST_GO",
            "manifest_sha256": verified.manifest_sha256,
            "candidate_sha": verified.candidate_sha,
            "host_control_verified": False}
