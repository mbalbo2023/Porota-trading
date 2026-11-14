#!/usr/bin/python3 -I
"""Typed root-owned entrypoint contract for Porota RC6 OCI custody.

This source must NOT be run as a privileged script from a deploy checkout.
An independently installed, immutable root-owned copy is required; the
entrypoint must fail closed if installation or approval is missing.

THREAT MODEL: porotaadmin's existing sudo-all capability is intentionally
preserved. This guard constrains the reviewed CI path, not an adversary who
already possesses unrestricted sudo. No sudoers, Docker socket, Docker group,
rootless Boca context or PPI Watch state may be changed by this module.

PROVISIONAL: root-owned installer, external Telegram/writer atestors and
systemd stage backend are intentionally unavailable and return HOLD79.
This is not a deploy-ready host control plane.
"""

from dataclasses import dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
from datetime import datetime, timedelta, timezone


class CustodyHold(RuntimeError):
    """Permission/authority failure with no Docker side effects (exit 79)."""

    exit_code = 79

    def __init__(self, reason):
        if re.fullmatch(r"[A-Z][A-Z0-9_]{0,70}", reason) is None:
            reason = "UNVERIFIED"
        self.reason = reason
        super().__init__("RC6_OCI_HOST_HOLD79:" + reason)


@dataclass(frozen=True)
class Command:
    namespace: str
    verb: str
    role: str | None = None
    expectations: tuple[str, ...] = ()
    attempt_token: str | None = None


_MANAGER_SIMPLE = frozenset({"verify-observer-sources"})
_MANAGER_ROLE = {
    "birth": frozenset({"dashboard", "observer"}),
    "retire": frozenset({"dashboard", "observer"}),
    "verify-newborn": frozenset({"dashboard", "observer"}),
    "stop": frozenset({"observer"}),
}
_CRITICAL_SIMPLE = frozenset({"birth", "attest-exclusivity"})
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_TAG = re.compile(r"[A-Za-z0-9_./:-]{1,160}\Z")
_IMMUTABLE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
_OBSERVER_SOURCE_PATHS = (
    "/app/cf_intraday_scalping.py", "/app/co_market_sessions_hf6.py",
)
_EXEC_ENV_CONTROL_KEYS = frozenset({
    "PATH", "ENV", "BASH_ENV", "BASHOPTS", "SHELLOPTS", "IFS", "CDPATH",
    "GCONV_PATH", "HOME", "SHELL", "RUBYOPT", "NODE_OPTIONS", "PERL5OPT",
    "PERL5LIB", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP",
    "PYTHONINSPECT", "PYTHONWARNINGS", "PYTHONUSERBASE",
    "PYTHONPYCACHEPREFIX", "PYTHONBREAKPOINT",
})
INSTALLED_EXECUTABLE = "/usr/local/sbin/porota-rc6-oci-custody"
INSTALLED_CUSTODY_FILE = "/usr/local/libexec/porota-rc6-custody/rc6_runtime_oci_custody.py"
INSTALLED_PROFILES_FILE = "/var/lib/porota-rc6-oci-custody/host-launch-profiles.json"
APPROVAL_FILE = "/var/lib/porota-rc6-oci-custody/host-helper-approval.json"
_APPROVAL_KEYS = frozenset({
    "schema_version", "phase", "candidate_sha", "candidate_tree", "artifact_digest",
    "image_id", "daemon_id", "generation", "run_id", "run_attempt",
    "paper_mode", "real_orders_allowed", "helper_sha256", "custody_sha256",
    "profiles_sha256", "unit_template_sha256", "expires_at",
})
_PREBIRTH_KEYS = frozenset({
    "schema", "status", "candidate_sha", "runtime_writers", "paper_mode",
    "real_orders_sent", "ppi_watch_accessed",
})


def _require(condition, reason="CLI_SHAPE"):
    if not condition:
        raise CustodyHold(reason)


def _reject_executable_env_control(key, value):
    """No PATH/interpreter/preload overrides from sealed or profile envs."""
    _require(type(key) is str and type(value) is str and
             key not in _EXEC_ENV_CONTROL_KEYS and
             not key.startswith(("LD_", "BASH_FUNC_", "GIT_CONFIG_")) and
             not (key.startswith("PYTHON") and key != "PYTHONDONTWRITEBYTECODE") and
             (key != "PYTHONDONTWRITEBYTECODE" or value == "1"),
             "ENV_CONTROL_KEY_FORBIDDEN")


def _validate_protected_inode(inode, *, expected_kind):
    """Reject non-root owners, aliases, writable code and non-regular files."""
    _require(getattr(inode, "st_uid", None) == 0 and
             not (inode.st_mode & 0o022), "ROOT_INODE_OWNER_OR_MODE")
    if expected_kind == "file":
        _require(stat.S_ISREG(inode.st_mode) and inode.st_nlink == 1 and
                 0 < inode.st_size <= 16 * 1024 * 1024, "ROOT_FILE_INODE")
    elif expected_kind == "dir":
        _require(stat.S_ISDIR(inode.st_mode), "ROOT_DIRECTORY_INODE")
    else:
        raise CustodyHold("ROOT_INODE_KIND")


def strict_approval_json(raw):
    """Independent receipt decoder; cannot load candidate custody to parse it."""
    _require(type(raw) is bytes and 0 < len(raw) <= 65536, "APPROVAL_SIZE")

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, "APPROVAL_DUPLICATE_KEY")
            result[key] = value
        return result

    def invalid_constant(_value):
        raise CustodyHold("APPROVAL_NONFINITE")

    try:
        doc = json.loads(raw.decode("utf-8", "strict"),
                         object_pairs_hook=unique_pairs,
                         parse_constant=invalid_constant)
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise CustodyHold("APPROVAL_JSON") from exc
    _require(type(doc) is dict, "APPROVAL_SCHEMA")
    return doc


def validate_independent_approval(doc, *, now, helper_digest, custody_digest,
                                  profiles_digest):
    """Pin code, attempt and PAPER policy to an independent root-owned intent."""
    _require(type(doc) is dict and set(doc) == _APPROVAL_KEYS and
             doc["schema_version"] == 1 and
             doc["phase"] in ("GENESIS", "CONTINUITY"), "APPROVAL_SCHEMA")
    _require(all(type(doc[key]) is str and
                 re.fullmatch(r"[0-9a-f]{40}", doc[key]) for key in
                 ("candidate_sha", "candidate_tree")), "APPROVAL_SOURCE")
    _require(all(type(doc[key]) is str and
                 re.fullmatch(r"sha256:[0-9a-f]{64}", doc[key]) for key in
                 ("artifact_digest", "image_id")), "APPROVAL_DIGEST")
    _require(type(doc["daemon_id"]) is str and
             re.fullmatch(r"[A-Za-z0-9:_./-]{6,128}", doc["daemon_id"]),
             "APPROVAL_DAEMON")
    _require(type(doc["generation"]) is str and
             re.fullmatch(r"[0-9a-f]{32}", doc["generation"]),
             "APPROVAL_GENERATION")
    _require(all(type(doc[key]) is str and
                 re.fullmatch(r"[1-9][0-9]{0,19}", doc[key]) for key in
                 ("run_id", "run_attempt")), "APPROVAL_RUN")
    _require(doc["paper_mode"] == "PRODUCTION_PAPER" and
             doc["real_orders_allowed"] is False, "APPROVAL_PAPER")
    _require(all(type(doc[key]) is str and
                 _HEX64.fullmatch(doc[key]) for key in
                 ("helper_sha256", "custody_sha256", "profiles_sha256",
                  "unit_template_sha256")), "APPROVAL_HASH")
    _require(doc["helper_sha256"] == helper_digest and
             doc["custody_sha256"] == custody_digest and
             doc["profiles_sha256"] == profiles_digest,
             "APPROVAL_INSTALLED_CODE_MISMATCH")
    _require(type(now) is datetime and now.tzinfo is not None,
             "APPROVAL_CLOCK")
    expiry = doc["expires_at"]
    _require(type(expiry) is str and
             re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", expiry),
             "APPROVAL_EXPIRY_FORMAT")
    try:
        expiration = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CustodyHold("APPROVAL_EXPIRY_FORMAT") from exc
    _require(now < expiration <= now + timedelta(hours=4),
             "APPROVAL_EXPIRED_OR_FUTURE")
    return doc


def derive_attempt_token(approval):
    """Correlation only, never a credential or source of authorization."""
    fields = ("candidate_sha", "candidate_tree", "artifact_digest", "generation",
              "run_id", "run_attempt", "daemon_id")
    _require(type(approval) is dict and all(type(approval.get(k)) is str for k in fields),
             "ATTEMPT_APPROVAL_SCHEMA")
    canonical = json.dumps({k: approval[k] for k in fields},
                           sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def verify_custody_identity(approval, custody):
    """The installed custody agrees with independent root intent and daemon."""
    _require(getattr(custody, "DOCKER_BINARY", None) == "/usr/bin/docker" and
             getattr(custody, "DOCKER_ENDPOINT", None) ==
             "unix:///var/run/docker.sock" and
             str(getattr(custody, "TRUSTED_ROOT", "")) ==
             "/var/lib/porota-rc6-oci-custody", "INSTALLED_DAEMON_MISMATCH")
    phase = approval["phase"]
    if phase == "GENESIS":
        doc = custody._read_trusted_genesis()
        _require(doc.get("state") == "LEGACY_UNTRUSTED" and
                 doc.get("authority") == "INDEPENDENT_HOST_CONTROL_PLANE",
                 "GENESIS_EXTERNAL_AUTHORITY")
    else:
        doc = custody._read_trusted_intent()
    for field in ("schema_version", "candidate_sha", "candidate_tree",
                  "artifact_digest", "image_id", "daemon_id", "generation",
                  "run_id", "run_attempt", "paper_mode", "real_orders_allowed"):
        _require(type(doc) is dict and doc.get(field) == approval[field],
                 "ROOT_INTENT_CHANGED")


class ApprovedLaunchProfiles:
    """Root-approved structured OCI profiles; never accepts raw Docker argv."""

    _ROLE_KEYS = frozenset({"env_file", "env_sha256", "environment", "mounts"})
    _PROGRAM = {"dashboard": "o_dashboard.py", "observer": "bv_paper_runtime.py",
                "critical": "/code/fm_critical_approval_unix_runtime_rc6.py"}
    _CRITICAL_REQUIRED_ENV = {
        "PYTHONDONTWRITEBYTECODE": "1",
        "POROTA_CRITICAL_APPROVAL_ENABLED": "true",
        "POROTA_TELEGRAM_SINGLE_CONSUMER_ENFORCED": "true",
        "POROTA_CRITICAL_APPROVAL_POLL_SECONDS": "10",
        "POROTA_CRITICAL_APPROVAL_DB": "/data/critical_approval_rc6.db",
        "POROTA_CRITICAL_GITHUB_SOCKET": "/run/control/github.sock",
        "POROTA_CRITICAL_BROKER_TOKEN_FILE": "/run/secrets/broker_capability.token",
        "POROTA_CRITICAL_TELEGRAM_TOKEN_FILE": "/run/secrets/critical_telegram.token",
        "POROTA_CRITICAL_TELEGRAM_CHAT_FILE": "/run/secrets/critical_telegram.chat",
    }
    _CRITICAL_REQUIRED_MOUNTS = frozenset({
        ("/opt/porota-control-plane-rc6/current", "/code", True),
        ("/opt/porota-control-plane-rc6/secrets/broker_capability.token",
         "/run/secrets/broker_capability.token", True),
        ("/opt/porota-control-plane-rc6/secrets/critical_telegram.token",
         "/run/secrets/critical_telegram.token", True),
        ("/opt/porota-control-plane-rc6/secrets/critical_telegram.chat",
         "/run/secrets/critical_telegram.chat", True),
        ("/opt/porota-control-plane-rc6/data", "/data", False),
        ("/run/porota-critical-approval-rc6", "/run/control", False),
    })
    _CRITICAL_HEALTH_CMD = (
        "python -I -c 'import sys; sys.path.insert(0, \"/code\"); "
        "from fm_critical_approval_unix_runtime_rc6 import UnixGithubIssuesClient; "
        "h=UnixGithubIssuesClient().health(); "
        "assert h.get(\"status\")==\"ok\" and "
        "h.get(\"capability\")==\"issues_only\"'"
    )

    def __init__(self, doc, *, candidate_sha, artifact_digest, image_id, generation):
        _require(type(doc) is dict and set(doc) in ({
            "schema_version", "candidate_sha", "artifact_digest", "image_tag",
            "root_stage", "roles"}, {
            "schema_version", "candidate_sha", "artifact_digest", "image_tag",
            "root_stage", "roles", "observer_source_sha256"}) and
            doc["schema_version"] == 1 and
            doc["candidate_sha"] == candidate_sha and
            doc["artifact_digest"] == artifact_digest and
            type(doc["image_tag"]) is str and
            re.fullmatch(r"porota-trading-bot:[A-Za-z0-9_.-]{1,180}",
                         doc["image_tag"]), "PROFILE_ROOT_SCHEMA")
        root = doc["root_stage"]
        _require(type(root) is str and
                 (root == "/opt/porota-trading" or
                  re.fullmatch(r"/opt/porota-trading/[A-Za-z0-9_./-]{1,120}", root)) and
                 "/../" not in root and not root.endswith("/..") and
                 "//" not in root, "PROFILE_STAGE_ROOT")
        roles = doc["roles"]
        _require(type(roles) is dict and set(roles) == set(self._PROGRAM),
                 "PROFILE_THREE_ROLES")
        _require(type(image_id) is str and
                 re.fullmatch(r"sha256:[0-9a-f]{64}", image_id),
                 "PROFILE_IMMUTABLE_IMAGE_ID")
        _require(type(generation) is str and
                 re.fullmatch(r"[0-9a-f]{32}", generation),
                 "PROFILE_GENERATION")
        for role, profile in roles.items():
            self._verify_profile(role, profile, root, generation)
        # Optional artifact-closure hashes are trusted ONLY when included in
        # the installed root-owned profiles file pinned by the approval SHA.
        # An older manifest without this proof remains a targeted HOLD79.
        source_hashes = doc.get("observer_source_sha256")
        if source_hashes is not None:
            _require(type(source_hashes) is dict and
                     set(source_hashes) == set(_OBSERVER_SOURCE_PATHS) and
                     all(type(value) is str and _HEX64.fullmatch(value)
                         for value in source_hashes.values()),
                     "PROFILE_OBSERVER_SOURCE_CLOSURE")
        self.observer_source_digests = source_hashes
        self.image_tag = doc["image_tag"]
        self.image_id = image_id
        self.root = root
        self.roles = roles

    @staticmethod
    def _clean_path(path):
        return (type(path) is str and 1 < len(path) <= 320 and
                path.startswith("/") and "//" not in path and "/../" not in path and
                not path.endswith("/..") and "/./" not in path and
                not path.endswith("/.") and "\n" not in path and
                "\r" not in path and ":" not in path)

    def _verify_profile(self, role, profile, root, generation):
        _require(type(profile) is dict and set(profile) == self._ROLE_KEYS,
                 "PROFILE_ROLE_SCHEMA")
        env_file = profile["env_file"]
        if role == "critical":
            _require(env_file is None and profile["env_sha256"] is None,
                     "CRITICAL_ENV_FILE_FORBIDDEN")
        else:
            expected = ("/var/lib/porota-rc6-oci-custody/generations/" +
                        generation + "/env/" + role + ".env")
            _require(env_file == expected and
                     type(profile["env_sha256"]) is str and
                     _HEX64.fullmatch(profile["env_sha256"]),
                     "PROFILE_ENV_NOT_SEALED")
        environment = profile["environment"]
        _require(type(environment) is dict and len(environment) <= 50,
                 "PROFILE_ENV_FORMAT")
        _require(environment.get("POROTA_RUNTIME_MODE") == "PRODUCTION_PAPER" and
                 environment.get("PPI_ORDERS") == "BLOCKED" and
                 environment.get("REAL_ORDERS_ALLOWED") == "false",
                 "PROFILE_PAPER_ZERO_REQUIRED")
        if role == "observer":
            _require(environment.get("MARKET_OPEN_HOUR") == "10" and
                     environment.get("MARKET_OPEN_MINUTE") == "30",
                     "PROFILE_BYMA_1030_REQUIRED")
        if role == "critical":
            _require(all(environment.get(key) == value
                         for key, value in self._CRITICAL_REQUIRED_ENV.items()),
                     "CRITICAL_RUNTIME_ENV_REQUIRED")
            _require(set(environment) ==
                     set(self._CRITICAL_REQUIRED_ENV) | {
                         "POROTA_RUNTIME_MODE", "PPI_ORDERS", "REAL_ORDERS_ALLOWED"},
                     "CRITICAL_ENV_SCOPE")
        for key, value in environment.items():
            _reject_executable_env_control(key, value)
            _require(type(key) is str and type(value) is str and
                     re.fullmatch(r"[A-Z][A-Z0-9_]{0,80}", key) and
                     len(value) <= 512 and "\n" not in value and
                     "\r" not in value and
                     (not key.startswith("REAL_") or
                      (key == "REAL_ORDERS_ALLOWED" and value == "false")) and
                     "LIVE_ORDER" not in key and
                     ("REAL_ORDER" not in key or key == "REAL_ORDERS_ALLOWED") and
                     "TRADING_ENABLED" not in key, "PROFILE_ENV_UNSAFE")
            _require(key in ("POROTA_RUNTIME_MODE", "SERVER_TIMEZONE", "DATA_DIR",
                             "REAL_ORDERS_ALLOWED",
                             "PPI_PRODUCTION_SECRET_FILE", "PYTHONDONTWRITEBYTECODE",
                              ) or key.startswith(("PAPER_", "MARKET_", "PPI_")) or
                     (role == "critical" and key in self._CRITICAL_REQUIRED_ENV),
                     "PROFILE_ENV_NOT_ALLOWLISTED")
            if key == "POROTA_RUNTIME_MODE":
                _require(value == "PRODUCTION_PAPER", "PROFILE_NOT_PAPER")
            if key == "PPI_ORDERS":
                _require(value == "BLOCKED", "PROFILE_ORDERS_NOT_BLOCKED")
        mounts = profile["mounts"]
        _require(type(mounts) is list and 0 <= len(mounts) <= 40,
                 "PROFILE_MOUNT_COUNT")
        for mount in mounts:
            _require(type(mount) is dict and set(mount) == {
                "source", "target", "readonly"}, "PROFILE_MOUNT_SCHEMA")
            source, target = mount["source"], mount["target"]
            _require(self._clean_path(source) and self._clean_path(target) and
                     type(mount["readonly"]) is bool, "PROFILE_MOUNT_PATH")
            if role == "critical":
                _require((source, target, mount["readonly"]) in
                         self._CRITICAL_REQUIRED_MOUNTS, "CRITICAL_MOUNT_SCOPE")
            else:
                _require((source == root or source.startswith(root + "/") or
                          source == "/opt/porota-operator" or
                          source.startswith("/opt/porota-control-plane-rc6/") or
                          source.startswith("/run/porota-critical-approval-rc6/")) and
                         (target == "/app/data" or target == "/app/operator" or
                          target == "/code" or
                          target.startswith("/run/secrets/") or
                          target.startswith("/run/control/")), "PROFILE_MOUNT_SCOPE")
            if target.startswith("/run/secrets/") or target == "/app/operator" or \
                    target == "/code":
                _require(mount["readonly"], "PROFILE_SECRET_OR_CODE_WRITABLE")
        if role == "critical":
            _require(len(mounts) == len(self._CRITICAL_REQUIRED_MOUNTS) and
                     {(mount["source"], mount["target"], mount["readonly"])
                      for mount in mounts} == self._CRITICAL_REQUIRED_MOUNTS,
                     "CRITICAL_MOUNT_REQUIRED")

    def preflight_launch_profiles(self):
        """Read-only closure check before retirement of any sealed predecessor."""
        for role in ("dashboard", "observer"):
            profile = self.roles[role]
            _verify_root_sealed_env(profile["env_file"], profile["env_sha256"])
        return True

    def launch_argv(self, role, *, name, cidfile, labels):
        _require(role in self._PROGRAM and type(labels) in (tuple, list) and
                 len(labels) % 2 == 0 and
                 all(labels[i] == "--label" for i in range(0, len(labels), 2)),
                 "PROFILE_LAUNCH_ROLE")
        # All roles must remain CREATED until immutable image, provenance,
        # labels and CID are verified by A1079. No pre-receipt execution.
        args = ["docker", "create"]
        args.extend(("--name", name, "--cidfile", cidfile, "--pull", "never",
                     "--user", "botuser", "--cap-drop", "ALL",
                     "--security-opt", "no-new-privileges:true"))
        if role in ("observer", "critical"):
            args.extend(("--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=32m"))
        if role != "critical":
            args.extend(("--restart", "unless-stopped"))
        if role == "dashboard":
            args.extend(("-p", "127.0.0.1:8000:8000"))
        if role == "observer":
            args.append("--no-healthcheck")
        if role == "critical":
            args.extend(("--init", "--pids-limit", "64", "--memory", "192m",
                         "--health-cmd", self._CRITICAL_HEALTH_CMD,
                         "--health-interval", "30s", "--health-timeout", "20s",
                         "--health-retries", "3", "--health-start-period", "20s"))
        args.extend(labels)
        profile = self.roles[role]
        if profile["env_file"] is not None:
            _verify_root_sealed_env(profile["env_file"], profile["env_sha256"])
            args.extend(("--env-file", profile["env_file"]))
        for key, value in sorted(profile["environment"].items()):
            args.extend(("-e", key + "=" + value))
        for mount in profile["mounts"]:
            args.extend(("--mount", "type=bind,source=" + mount["source"] +
                         ",target=" + mount["target"] +
                         (",readonly" if mount["readonly"] else "")))
        args.extend(("--entrypoint", "python", self.image_id,
                     self._PROGRAM[role]))
        return tuple(args)


class InstalledApprovedAuthority(ApprovedLaunchProfiles):
    """Only the independently root-approved manifest can grant OCI profiles."""

    def __init__(self, approval, profiles_document):
        super().__init__(profiles_document,
                         candidate_sha=approval["candidate_sha"],
                         artifact_digest=approval["artifact_digest"],
                         image_id=approval["image_id"],
                         generation=approval["generation"])
        self.phase = approval["phase"]
        self.candidate_sha = approval["candidate_sha"]
        self.candidate_tree = approval["candidate_tree"]
        self.generation = approval["generation"]
        self.run_id = approval["run_id"]
        self.run_attempt = approval["run_attempt"]
        self.artifact_digest = approval["artifact_digest"]
        self.daemon_id = approval["daemon_id"]
        self.unit_template_sha256 = approval["unit_template_sha256"]
        self.attempt_token = derive_attempt_token(approval)

    def stage_protector(self, *, source, expected_mainpid):
        return _stage_critical_protector(self, source=source,
                                          expected_mainpid=expected_mainpid)


def _bind_approved_authority(approval, profiles_bytes, custody):
    verify_custody_identity(approval, custody)
    authority = InstalledApprovedAuthority(
        approval, strict_approval_json(profiles_bytes))
    _require(callable(getattr(custody, "_docker_run", None)) and
             callable(getattr(custody, "begin_genesis", None)) and
             callable(getattr(custody, "begin_attempt", None)),
             "CUSTODY_INTERFACE_UNVERIFIED")
    return HostHelperController(authority, custody, custody._docker_run)


def _stage_critical_protector(_authority, *, source, expected_mainpid):
    """Explicitly block physical stage until independently reviewed installer.

    The source template and PID are *expectations*, never authority to write
    systemd. This review-only implementation must not run systemctl, overwrite
    a unit, restart the existing critical process, or adopt a legacy name.
    """
    _require(type(source) is str and type(expected_mainpid) is int and
             expected_mainpid > 0, "SYSTEMD_STAGE_EXPECTATIONS")
    raise CustodyHold("SYSTEMD_STAGE_INSTALLER_UNVERIFIED")


def _import_installed_custody():
    _require(sys.flags.isolated == 1, "PYTHON_NOT_ISOLATED")
    # Path is a fixed root-owned file previously hashed under trusted approval.
    # Never import scripts.rc6_runtime_oci_custody from the caller's checkout.
    name = "_porota_rc6_oci_custody_root_installed"
    spec = importlib.util.spec_from_file_location(name, INSTALLED_CUSTODY_FILE)
    _require(spec is not None and spec.loader is not None,
             "CUSTODY_INSTALLED_LOADER")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise CustodyHold("CUSTODY_INSTALLED_IMPORT") from exc
    return module


def _read_root_owned(path, *, expected_mode=None):
    """Bounded inode read from fixed root-owned path, rejecting symlink chains."""
    target = Path(path)
    _require(target.is_absolute() and target.parts[0] == "/", "ROOT_PATH")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    directory_flags = flags | os.O_DIRECTORY
    fd = os.open("/", directory_flags)
    try:
        for component in target.parts[1:-1]:
            _require(component not in ("", ".", ".."), "ROOT_PATH")
            next_fd = os.open(component, directory_flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
            _validate_protected_inode(os.fstat(fd), expected_kind="dir")
        _require(target.name not in ("", ".", ".."), "ROOT_PATH")
        source_fd = os.open(target.name, flags, dir_fd=fd)
        try:
            inode = os.fstat(source_fd)
            _validate_protected_inode(inode, expected_kind="file")
            if expected_mode is not None:
                _require(inode.st_mode & 0o777 == expected_mode,
                         "ROOT_FILE_MODE_UNVERIFIED")
            raw = os.read(source_fd, 16 * 1024 * 1024 + 1)
            after = os.fstat(source_fd)
            _require(len(raw) == inode.st_size and len(raw) <= 16 * 1024 * 1024 and
                     after.st_ino == inode.st_ino and after.st_size == inode.st_size,
                     "ROOT_FILE_CHANGED")
            return raw
        finally:
            os.close(source_fd)
    except OSError as exc:
        raise CustodyHold("ROOT_FILE_MISSING_OR_ALIAS") from exc
    finally:
        os.close(fd)


def _verify_root_sealed_env(path, expected_sha256):
    """Read a pinned 0400 env closure, never a mutable checkout/DATA file."""
    _require(type(path) is str and
             re.fullmatch(
                 r"/var/lib/porota-rc6-oci-custody/generations/[0-9a-f]{32}/env/"
                 r"(?:dashboard|observer)\.env", path) and
             type(expected_sha256) is str and _HEX64.fullmatch(expected_sha256),
             "ENV_SEAL_PATH_OR_DIGEST")
    raw = _read_root_owned(path, expected_mode=0o400)
    _require(len(raw) <= 65536 and
             hashlib.sha256(raw).hexdigest() == expected_sha256,
             "ENV_SEAL_DIGEST_MISMATCH")
    try:
        data = raw.decode("utf-8", "strict")
    except UnicodeError as exc:
        raise CustodyHold("ENV_SEAL_ENCODING") from exc
    found = {}
    for line in data.splitlines():
        _require(line and "=" in line and "\x00" not in line and
                 "\r" not in line, "ENV_SEAL_LINE")
        key, value = line.split("=", 1)
        _reject_executable_env_control(key, value)
        _require(re.fullmatch(r"[A-Z][A-Z0-9_]{0,80}", key) and
                 key not in found and len(value) <= 1024 and
                 (key == "REAL_ORDERS_ALLOWED" or
                  not any(marker in key for marker in (
                      "LIVE", "EXEC_REAL", "REAL_TRADING", "REAL_ORDER",
                      "TRADING_ENABLED", "BROKER_SEND", "PRODUCTION_ORDER"))),
                 "ENV_SEAL_KEY_UNSAFE")
        found[key] = value
    _require(found.get("POROTA_RUNTIME_MODE") == "PRODUCTION_PAPER" and
             found.get("PPI_ORDERS") == "BLOCKED" and
             found.get("REAL_ORDERS_ALLOWED") == "false",
             "ENV_SEAL_PAPER_ZERO")
    if path.endswith("/observer.env"):
        _require(found.get("MARKET_OPEN_HOUR") == "10" and
                 found.get("MARKET_OPEN_MINUTE") == "30",
                 "ENV_SEAL_MARKET_SCHEDULE")


def _load_installed_authority(*, now=None):
    """Verify external approval BEFORE importing any privileged OCI code."""
    raw_approval = _read_root_owned(APPROVAL_FILE)
    helper_bytes = _read_root_owned(INSTALLED_EXECUTABLE)
    custody_bytes = _read_root_owned(INSTALLED_CUSTODY_FILE)
    profiles_bytes = _read_root_owned(INSTALLED_PROFILES_FILE)
    approval = validate_independent_approval(
        strict_approval_json(raw_approval),
        now=datetime.now(timezone.utc) if now is None else now,
        helper_digest=hashlib.sha256(helper_bytes).hexdigest(),
        custody_digest=hashlib.sha256(custody_bytes).hexdigest(),
        profiles_digest=hashlib.sha256(profiles_bytes).hexdigest())
    # The imported module and OCI profiles must be independently provisioned;
    # never fall back to cwd, PYTHONPATH, or repo source on any failure.
    custody = _import_installed_custody()
    return _bind_approved_authority(approval, profiles_bytes, custody)


def _expect_flags(actual, names):
    _require(len(actual) == 2 * len(names))
    _require(tuple(actual[::2]) == names)
    return tuple(actual[1::2])


def parse_command(argv):
    """Allow only fixed verbs, roles and bounded expectations; no Docker argv."""
    _require(type(argv) in (tuple, list) and 0 < len(argv) <= 12 and
             all(type(arg) is str and 0 < len(arg) <= 300 for arg in argv))
    args = tuple(argv)
    if len(args) == 1 and args[0] in ("status", "seal"):
        return Command("global", args[0])
    if args[0] == "verify" and len(args) == 2 and args[1] in (
            "dashboard", "observer", "critical"):
        return Command("global", "verify", args[1])
    if args[0] == "verify-current":
        values = _expect_flags(args[1:], (
            "--candidate-sha", "--candidate-tree", "--generation",
            "--artifact-digest", "--image-id"))
        _require(_HEX40.fullmatch(values[0]) and
                 _HEX40.fullmatch(values[1]) and
                 re.fullmatch(r"[0-9a-f]{32}", values[2]) and
                 _IMMUTABLE_ID.fullmatch(values[3]) and
                 _IMMUTABLE_ID.fullmatch(values[4]),
                 "POSTSEAL_CLI_IDENTITY")
        return Command("global", "verify-current", expectations=values)
    _require(len(args) >= 2)
    namespace, verb = args[:2]
    remaining = args[2:]
    if namespace == "manager":
        if verb == "begin":
            _require(not remaining)
            return Command(namespace, verb)
        _require(len(remaining) >= 2 and remaining[-2] == "--attempt" and
                 re.fullmatch(r"[0-9a-f]{32}", remaining[-1]),
                 "ATTEMPT_EXPECTATION_REQUIRED")
        attempt = remaining[-1]
        remaining = remaining[:-2]
        if verb in _MANAGER_SIMPLE:
            _require(not remaining)
            return Command(namespace, verb, attempt_token=attempt)
        if verb in _MANAGER_ROLE:
            _require(len(remaining) == 1 and
                     remaining[0] in _MANAGER_ROLE[verb])
            return Command(namespace, verb, remaining[0], attempt_token=attempt)
        if verb == "verify-image":
            _require(len(remaining) == 2 and
                     _TAG.fullmatch(remaining[0]) and
                     _IMMUTABLE_ID.fullmatch(remaining[1]))
            return Command(namespace, verb, expectations=remaining,
                           attempt_token=attempt)
        raise CustodyHold("CLI_SHAPE")
    if namespace == "critical":
        if verb in _CRITICAL_SIMPLE:
            _require(not remaining)
            return Command(namespace, verb)
        if verb in ("describe-stage-intent", "stage-approved-protector"):
            _require(len(remaining) == 3 and remaining[:2] ==
                     ("--paper-only", "--source-sha") and
                     _HEX40.fullmatch(remaining[2]))
            return Command(namespace, verb, expectations=("true", remaining[2]))
        if verb == "stage-protector":
            values = _expect_flags(remaining, ("--unit-template", "--candidate-sha256",
                                                "--expected-mainpid", "--source-sha"))
            _require(len(values[0]) <= 256 and values[0].startswith("/tmp/rc6-critical-unit.")
                     and values[0].endswith("/porota-critical-approval-rc6.service") and
                     _HEX64.fullmatch(values[1]) and
                     re.fullmatch(r"[1-9][0-9]{0,9}", values[2]) and
                     _HEX40.fullmatch(values[3]))
            return Command(namespace, verb, expectations=values)
        if verb == "start-newborn":
            _require(len(remaining) == 5 and remaining[:2] ==
                     ("--paper-only", "--source-sha") and
                     remaining[3] == "--generation" and
                     _HEX40.fullmatch(remaining[2]) and
                     re.fullmatch(r"[0-9a-f]{32}", remaining[4]))
            return Command(namespace, verb,
                           expectations=("true", remaining[2], remaining[4]))
        raise CustodyHold("CLI_SHAPE")
    if namespace == "quiescence":
        if verb in ("verify-prebirth", "verify-receipt") and not remaining:
            return Command(namespace, verb)
        _require(verb in ("stop", "start") and len(remaining) == 1 and
                 remaining[0] in ("dashboard", "observer"))
        return Command(namespace, verb, remaining[0])
    if namespace == "image":
        if verb == "preflight-cleanup" and not remaining:
            return Command(namespace, verb)
        raise CustodyHold("IMAGE_CLEANUP_UNPROVEN")
    raise CustodyHold("CLI_SHAPE")


class HostHelperController:
    """One typed adapter to the installed A1079 custody, not a second ledger."""

    def __init__(self, approved_authority, installed_custody, docker_call):
        self.authority = approved_authority
        self.custody = installed_custody
        self.docker = docker_call

    def _session(self):
        _require(self.authority.phase in ("GENESIS", "CONTINUITY"),
                 "APPROVED_PHASE")
        if self.authority.phase == "GENESIS":
            return self.custody.begin_genesis()
        return self.custody.begin_attempt()

    def _verify_external_prebirth(self):
        """Consume a physical host attestor; never infer it from CI/source.

        The custody backend is imported exclusively from the root-owned,
        independently hash-approved installation. Missing backend capability
        must HOLD before any OCI session/journal or Docker access. This is a
        receipt verifier, not an attestor or a host-control approval.
        """
        attest = getattr(self.custody, "attest_prebirth", None)
        _require(callable(attest), "EXTERNAL_QUIESCENCE_ATTESTOR_UNVERIFIED")
        keys = ("candidate_sha", "candidate_tree", "generation",
                "artifact_digest", "image_id", "daemon_id", "run_id",
                "run_attempt")
        expected = {key: getattr(self.authority, key, None) for key in keys}
        _require(type(expected["candidate_sha"]) is str and
                 _HEX40.fullmatch(expected["candidate_sha"]) and
                 type(expected["candidate_tree"]) is str and
                 _HEX40.fullmatch(expected["candidate_tree"]) and
                 type(expected["generation"]) is str and
                 re.fullmatch(r"[0-9a-f]{32}", expected["generation"]) and
                 all(type(expected[key]) is str and
                     _IMMUTABLE_ID.fullmatch(expected[key]) for key in
                     ("artifact_digest", "image_id")) and
                 type(expected["daemon_id"]) is str and
                 re.fullmatch(r"[A-Za-z0-9:_./-]{6,128}", expected["daemon_id"]) and
                 all(type(expected[key]) is str and
                     re.fullmatch(r"[1-9][0-9]{0,19}", expected[key]) for key in
                     ("run_id", "run_attempt")),
                 "EXTERNAL_QUIESCENCE_INTENT_UNVERIFIED")
        proof = attest(**expected)
        _require(type(proof) is dict and set(proof) == _PREBIRTH_KEYS and
                 proof["schema"] == "RC6_PREBIRTH_QUIESCENCE_V1" and
                 proof["status"] == "OK" and
                 type(proof["candidate_sha"]) is str and
                 proof["candidate_sha"] == expected["candidate_sha"] and
                 proof["runtime_writers"] == "QUIESCENT" and
                 proof["paper_mode"] == "PRODUCTION_PAPER" and
                 type(proof["real_orders_sent"]) is int and
                 proof["real_orders_sent"] == 0 and
                 proof["ppi_watch_accessed"] is False,
                 "EXTERNAL_QUIESCENCE_RECEIPT_UNVERIFIED")
        result = json.dumps(proof, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=True)
        _require(len(result.encode("ascii")) <= 2048,
                 "EXTERNAL_QUIESCENCE_RECEIPT_UNVERIFIED")
        return result

    def _verify_current_sealed(self, command):
        # This command intentionally bypasses begin_attempt(): A1079's
        # attempt session proves the PREVIOUS receipt and fails replay after
        # seal. A1079's separately reviewed CURRENT API owns all root files,
        # journals, CIDfile provenance and exact-CID Docker inspection.
        _require(self.authority.phase in ("GENESIS", "CONTINUITY"),
                 "APPROVED_PHASE")
        fields = ("candidate_sha", "candidate_tree", "generation",
                  "artifact_digest", "image_id")
        expected = tuple(getattr(self.authority, key, None) for key in fields)
        _require(command.expectations == expected,
                 "POSTSEAL_EXPECTATIONS_MISMATCH")
        _require(type(self.authority.daemon_id) is str and
                 re.fullmatch(r"[A-Za-z0-9:_./-]{6,128}", self.authority.daemon_id),
                 "POSTSEAL_DAEMON_UNVERIFIED")
        verify = getattr(self.custody, "verify_current_sealed", None)
        _require(callable(verify), "POSTSEAL_CUSTODY_API_UNAVAILABLE")
        running = {role: "running" for role in ("dashboard", "observer", "critical")}
        proof = verify(candidate_sha=expected[0], candidate_tree=expected[1],
                       generation=expected[2], artifact_digest=expected[3],
                       image_id=expected[4], daemon_id=self.authority.daemon_id,
                       expected_role_states=running)
        identifiers = {**dict(zip(fields, expected)),
                       "daemon_id": self.authority.daemon_id,
                       "schema": "RC6_POSTSEAL_CUSTODY_V1", "status": "OK",
                       "declared_paper_mode": "PRODUCTION_PAPER"}
        _require(type(proof) is dict and
                 set(proof) == set(identifiers) | {"roles"} and
                 all(type(proof.get(key)) is str and proof[key] == value
                     for key, value in identifiers.items()) and
                 type(proof.get("roles")) is dict and
                 set(proof["roles"]) == set(running),
                 "POSTSEAL_CUSTODY_RECEIPT_UNVERIFIED")
        cids = []
        for role in running:
            record = proof["roles"][role]
            _require(type(record) is dict and set(record) == {"cid", "state"} and
                     type(record["cid"]) is str and
                     _HEX64.fullmatch(record["cid"]) and
                     type(record["state"]) is str and
                     record["state"] == "running",
                     "POSTSEAL_CUSTODY_RECEIPT_UNVERIFIED")
            cids.append(record["cid"])
        _require(len(set(cids)) == 3, "POSTSEAL_CUSTODY_RECEIPT_UNVERIFIED")
        result = json.dumps(proof, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=True)
        _require(len(result.encode("ascii")) <= 4096,
                 "POSTSEAL_CUSTODY_RECEIPT_UNVERIFIED")
        return result

    def _preflight_approved_profiles(self):
        # This is repeated for verify-image, retire and EACH birth, closing
        # both direct-CLI retire bypass and env swap after image verification.
        self._require_observer_source_closure()
        check = getattr(self.authority, "preflight_launch_profiles", None)
        _require(callable(check), "ROOT_PRELAUNCH_PREFLIGHT_UNAVAILABLE")
        _require(check() is True, "ROOT_PRELAUNCH_PREFLIGHT_UNVERIFIED")
        _require(type(self.authority.image_tag) is str and
                 type(self.authority.image_id) is str and
                 _IMMUTABLE_ID.fullmatch(self.authority.image_id) and
                 callable(getattr(self.custody, "_daemon_identity", None)) and
                 self.custody._daemon_identity() == self.authority.daemon_id,
                 "IMAGE_DAEMON_CHANGED")
        observed = self.docker(("docker", "image", "inspect", "--format", "{{.Id}}",
                                self.authority.image_tag), capture=True)
        output = getattr(observed, "stdout", None)
        _require(type(output) is str and len(output) <= 128 and
                 output.strip() == self.authority.image_id,
                 "IMAGE_TAG_BINDING_CHANGED")

    def _birth(self, session, role):
        # The caller has no image, mount, environment, socket, or name input.
        # The approved authority builds a fixed per-role OCI launch profile.
        self._preflight_approved_profiles()
        cidfile = str(session.prepare_birth(role))
        name = session.birth_name(role)
        labels = session.birth_labels(role)
        _require(re.fullmatch(r"porota_rc6_" + role + r"_g_[0-9a-f]{32}",
                              name) is not None, "BIRTH_NAME_FORMAT")
        _require(cidfile.startswith("/var/lib/porota-rc6-oci-custody/generations/")
                 and cidfile.endswith("/" + role + ".cid"), "BIRTH_CIDFILE_PATH")
        argv = self.authority.launch_argv(role, name=name, cidfile=cidfile,
                                          labels=labels)
        _require(type(argv) in (tuple, list) and len(argv) >= 7 and
                 argv[:2] == ("docker", "create") and
                 "--cidfile" in argv and "--name" in argv and
                 all(type(arg) is str and 0 < len(arg) <= 2048 for arg in argv),
                 "LAUNCH_PROFILE_INVALID")
        _require(argv[argv.index("--cidfile") + 1] == cidfile and
                 argv[argv.index("--name") + 1] == name and
                 argv[-2] == self.authority.image_id,
                 "LAUNCH_BIRTH_NOT_BOUND")
        result = self.docker(tuple(argv), capture=True)
        cid = getattr(result, "stdout", "").strip()
        _require(type(cid) is str and _HEX64.fullmatch(cid),
                 "LAUNCH_CID_NOT_PROVEN")
        session.record_birth(role, cid)
        if role != "critical":
            # A1079 re-verifies immutable Image ID, provenance and daemon
            # while CREATED. Only then is an exact-CID start authorized.
            session.authorize_newborn_start(role)
            session.verify_pending_birth(role)
        return "RC6_CUSTODY=OK"

    def _verify_image(self, command):
        tag, image_id = command.expectations
        _require(tag == self.authority.image_tag and
                 image_id == self.authority.image_id and
                 _IMMUTABLE_ID.fullmatch(image_id),
                 "IMAGE_ROOT_APPROVAL_MISMATCH")
        self._preflight_approved_profiles()
        # The verified tag remains only an expectation; CREATE always uses
        # the independent root-approved immutable image ID, never a tag.
        return "RC6_CUSTODY=OK"

    def _verify_newborn(self, session, role):
        # A1079 proves CIDFILE, labels, source artifact, rootful daemon,
        # original CREATE provenance and immutable image under its root lock.
        observed = session.verify_pending_birth(role)
        _require(type(observed) is dict and
                 type(observed.get("Id")) is str and
                 _HEX64.fullmatch(observed["Id"]) and
                 observed.get("Image") == self.authority.image_id and
                 type(observed.get("State")) is dict and
                 observed["State"].get("Status") == "running",
                 "NEWBORN_IDENTITY_OR_STATE_UNVERIFIED")
        return "RC6_CUSTODY=OK"

    @staticmethod
    def _verified_sealed_state(session, role):
        # Only A1079 can resolve an exact prior sealed CID. Docker names,
        # caller-supplied CIDs and a synthetic genesis inventory are forbidden.
        observed = session.verify_role(role)
        _require(type(observed) is dict and
                 type(observed.get("Id")) is str and
                 _HEX64.fullmatch(observed["Id"]) and
                 type(observed.get("State")) is dict and
                 observed["State"].get("Status") in ("running", "exited"),
                 "SEALED_ROLE_STATE_UNVERIFIED")
        return observed["Id"], observed["State"]["Status"]

    def _retire_sealed(self, session, role):
        _require(self.authority.phase == "CONTINUITY",
                 "LEGACY_UNTRUSTED_NO_MUTATION")
        self._preflight_approved_profiles()
        original_cid, state = self._verified_sealed_state(session, role)
        if state == "running":
            session.authorize_operation(role, "stop")
            stopped_cid, state = self._verified_sealed_state(session, role)
            _require(stopped_cid == original_cid and state == "exited",
                     "RETIRE_STOP_POSTSTATE_UNVERIFIED")
        # A1079 verifies the exact old sealed CID and journals REMOVE; no
        # host-wide list, by-name remove or cleanup based on legacy labels.
        session.authorize_operation(role, "remove")
        return "RC6_CUSTODY=OK"

    def _stop_sealed_observer(self, session):
        _require(self.authority.phase == "CONTINUITY",
                 "LEGACY_UNTRUSTED_NO_MUTATION")
        original_cid, state = self._verified_sealed_state(session, "observer")
        if state == "running":
            session.authorize_operation("observer", "stop")
            stopped_cid, state = self._verified_sealed_state(session, "observer")
            _require(stopped_cid == original_cid and state == "exited",
                     "OBSERVER_STOP_POSTSTATE_UNVERIFIED")
        # Already exited is a read-only idempotent success only for a trusted
        # sealed exact CID, never for a legacy name or an unverified absence.
        return "RC6_CUSTODY=OK"

    def _require_observer_source_closure(self):
        hashes = getattr(self.authority, "observer_source_digests", None)
        _require(type(hashes) is dict and
                 set(hashes) == set(_OBSERVER_SOURCE_PATHS) and
                 all(type(value) is str and _HEX64.fullmatch(value)
                     for value in hashes.values()),
                 "OBSERVER_SOURCE_CLOSURE_NOT_APPROVED")
        return hashes

    def _verify_observer_sources(self, session):
        hashes = self._require_observer_source_closure()
        self._verify_newborn(session, "observer")
        for path in _OBSERVER_SOURCE_PATHS:
            # A1079 permits precisely these two sha256sum exec argv profiles,
            # resolves pending CID under lock and journals read-only execution.
            observed = session.authorize_newborn_operation(
                "observer", "exec", "/usr/bin/sha256sum", path, capture=True)
            stdout = getattr(observed, "stdout", None)
            _require(type(stdout) is str and len(stdout) <= 256 and
                     stdout.rstrip("\n") == hashes[path] + "  " + path,
                     "OBSERVER_SOURCE_HASH_MISMATCH")
        return "RC6_CUSTODY=OK"

    def dispatch(self, command):
        _require(isinstance(command, Command), "CLI_SHAPE")
        if command.namespace == "global" and command.verb == "verify-current":
            return self._verify_current_sealed(command)
        if command.namespace == "global" and command.verb == "verify":
            # A1079 begin_attempt().verify_role() attests the PREVIOUS sealed
            # generation and fails GENERATION_REPLAY after seal. Until A1079
            # provides an approved exact-CID CURRENT receipt verifier, never
            # present an old container or a postseal error as a successful
            # proof of the new approved generation.
            raise CustodyHold("SEALED_CURRENT_CUSTODY_API_UNAVAILABLE")
        if command.namespace == "critical" and command.verb == "stage-approved-protector":
            _require(command.expectations == ("true", self.authority.candidate_sha),
                     "CRITICAL_STAGE_APPROVAL_MISMATCH")
            # Promote supplies no unit bytes or current PID. Even a pinned
            # source and independently approved hash cannot prove a trusted
            # root installer or unchanged critical process. HOLD before
            # beginning an OCI custody session or touching host state.
            raise CustodyHold("SYSTEMD_STAGE_INSTALLER_UNVERIFIED")
        if command.namespace == "quiescence" and command.verb == "verify-prebirth":
            return self._verify_external_prebirth()
        if command.namespace == "quiescence" and command.verb == "verify-receipt":
            # POST-SEAL needs independent 3-CID/journal writer evidence.
            # The installed adapter cannot currently prove this. Deny before
            # opening A1079 session/journal or acquiring its host lock.
            raise CustodyHold("EXTERNAL_QUIESCENCE_ATTESTOR_UNVERIFIED")
        if command.namespace == "image":
            # No global prune or image removal by name/tag; shared refs unknown.
            # A rejected cleanup must not even begin a root custody session.
            raise CustodyHold("OWNED_IMAGE_REFERENCE_PROOF_UNVERIFIED")
        session = self._session()
        if command.namespace == "manager" and command.verb != "begin":
            _require(type(self.authority.attempt_token) is str and
                     re.fullmatch(r"[0-9a-f]{32}", self.authority.attempt_token) and
                     command.attempt_token == self.authority.attempt_token,
                     "ATTEMPT_CHANGED_OR_UNVERIFIED")
        if command.namespace == "manager" and command.verb == "begin":
            return ("RC6_CUSTODY=" + self.authority.phase +
                    "\nRC6_CUSTODY_ATTEMPT=" + self.authority.attempt_token)
        if command.namespace == "manager" and command.verb == "verify-image":
            return self._verify_image(command)
        if command.namespace == "manager" and command.verb == "verify-newborn":
            return self._verify_newborn(session, command.role)
        if command.namespace == "manager" and command.verb == "verify-observer-sources":
            return self._verify_observer_sources(session)
        if command.namespace == "manager" and command.verb == "stop":
            return self._stop_sealed_observer(session)
        if command.verb in ("birth",) and command.namespace in ("manager", "critical"):
            role = command.role if command.namespace == "manager" else "critical"
            return self._birth(session, role)
        if command.namespace == "manager" and command.verb == "retire":
            return self._retire_sealed(session, command.role)
        if command.namespace == "critical" and command.verb == "start-newborn":
            paper, source_sha, generation = command.expectations
            _require(paper == "true" and
                     source_sha == self.authority.candidate_sha and
                     generation == self.authority.generation,
                     "CRITICAL_START_INTENT_MISMATCH")
            # A1079 checks an external, fresh, independent proof of exclusive
            # Telegram consumption before any exact-CID start. This adapter
            # must never mint or infer that proof.
            session.authorize_newborn_start("critical")
            return "RC6_CUSTODY=OK"
        if command.namespace == "critical" and command.verb == "stage-protector":
            template_path, unit_sha, pid, source_sha = command.expectations
            _require(source_sha == self.authority.candidate_sha and
                     unit_sha == self.authority.unit_template_sha256,
                     "CRITICAL_STAGE_APPROVAL_MISMATCH")
            stage = self.authority.stage_protector(source=template_path,
                                                   expected_mainpid=int(pid))
            _require(type(stage) is dict and stage.get("stage") == "STAGED" and
                     stage.get("mainpid_unchanged") is True,
                     "CRITICAL_STAGE_NOT_VERIFIED")
            return "RC6_CUSTODY=STAGED"
        if command.namespace == "critical" and command.verb == "describe-stage-intent":
            paper, source_sha = command.expectations
            _require(paper == "true" and source_sha == self.authority.candidate_sha and
                     re.fullmatch(r"[0-9a-f]{32}", self.authority.generation) and
                     re.fullmatch(r"[1-9][0-9]{0,19}", self.authority.run_id) and
                     re.fullmatch(r"[1-9][0-9]{0,19}", self.authority.run_attempt) and
                     re.fullmatch(r"sha256:[0-9a-f]{64}", self.authority.artifact_digest),
                     "CRITICAL_STAGE_INTENT_UNVERIFIED")
            return ("RC6_STAGE_INTENT_V1 " + self.authority.generation + " " +
                    self.authority.run_id + " " + self.authority.run_attempt + " " +
                    self.authority.artifact_digest + " porota_rc6_critical_g_" +
                    self.authority.generation)
        if command.namespace == "quiescence":
            _require(self.authority.phase == "CONTINUITY",
                     "LEGACY_UNTRUSTED_NO_MUTATION")
            before = session.verify_role(command.role)
            before_id = before.get("Id")
            before_status = (before.get("State") or {}).get("Status")
            _require(type(before_id) is str and _HEX64.fullmatch(before_id) and
                     before_status in ("running", "exited"),
                     "QUIESCENCE_PRESTATE")
            _require(before_status == ("running" if command.verb == "stop"
                                       else "exited"), "QUIESCENCE_PRESTATE")
            session.authorize_operation(command.role, command.verb)
            after = session.verify_role(command.role)
            after_status = (after.get("State") or {}).get("Status")
            _require(after.get("Id") == before_id and
                     after_status == ("exited" if command.verb == "stop"
                                      else "running"), "QUIESCENCE_POSTSTATE")
            _require(re.fullmatch(r"[0-9a-f]{32}", self.authority.generation),
                     "GENERATION_FORMAT")
            return json.dumps({
                "schema_version": 1, "role": command.role,
                "generation": self.authority.generation,
                "cid_fingerprint": hashlib.sha256(
                    bytes.fromhex(before_id)).hexdigest()[:12],
                "pre_status": before_status, "post_status": after_status,
            }, sort_keys=True, separators=(",", ":"))
        if command.namespace == "global" and command.verb == "seal":
            result = session.seal_generation()
            _require(result.get("sealed") is True and result.get("role_count") == 3,
                     "SEAL_THREE_ROLES_UNVERIFIED")
            return "RC6_CUSTODY=OK"
        raise CustodyHold("CLI_OPERATION_NOT_IMPLEMENTED")


def run_entrypoint(argv, *, executable_path=None):
    """Do not even load helper authority from a candidate checkout."""
    parse_command(argv)
    executable = sys.argv[0] if executable_path is None else executable_path
    _require(executable == INSTALLED_EXECUTABLE, "NOT_INSTALLED")
    _require(os.geteuid() == 0, "ROOT_REQUIRED")
    authority = _load_installed_authority()
    return authority.dispatch(parse_command(argv))


def main(argv=None):
    if argv is None:
        argv = tuple(sys.argv[1:])
    try:
        output = run_entrypoint(argv)
        _require(type(output) is str and 0 < len(output) <= 2048,
                 "UNBOUNDED_RESPONSE")
        print(output)
        return 0
    except CustodyHold as exc:
        print(str(exc), file=sys.stderr)
        return 79
    except Exception:
        # Never leak host paths, Docker output, secrets, env, or arbitrary CIDs.
        print("RC6_OCI_HOST_HOLD79:UNVERIFIED", file=sys.stderr)
        return 79


if __name__ == "__main__":
    sys.exit(main())
