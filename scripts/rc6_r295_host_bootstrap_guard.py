#!/usr/bin/env python3
"""R295 trusted-main root-bootstrap authority guard; no deployment GO.

This module checks a frozen operator receipt, protected host-identity pins,
and the EXACT R280 installer/contract bytes.  It never derives the root trust
pin from a candidate, a dispatch field, or a newly uploaded package.  Even a
successful bootstrap is INSTALLED_FILES_ONLY, not host_control_verified.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import subprocess
import stat
import sys
import tempfile


REPOSITORY = "mbalbo2023/Porota-trading"
ISSUE = 1093
OWNER = "mbalbo2023"
OWNER_GO_MARKER = "RC6_R295_HOST_BOOTSTRAP_GO_V1="
TRUSTED_INSTALLER_CLI_SHA256 = (
    "0d46b0f99ce09fdf4d851255e79fba4fe8af98fd7ab62c98f3c1938c484f8c7a"
)
TRUSTED_INSTALLER_CONTRACT_SHA256 = (
    "8ef4f5892560e496a85cae1df159e45b843d9f6047c3d87f68bc0b369f61adc9"
)
SOURCE_FILES = {
    "cli": "rc6_host_rootowned_installer_cli.py",
    "contract": "rc6_host_rootowned_installer_contract.py",
}
# Approved physical targets are fixed code infrastructure only. The new
# bundle/helper/manifest/candidate/D/E stay entirely OUT of this bootstrap.
ROOT_LOCK_DIR = "/var/lib/.porota-rc6-r297-bootstrap-reserved"
ROOT_RECEIPT = "/var/lib/porota-rc6-oci-custody/bootstrap-r297-complete.json"
ROOT_RECEIPT_STAGE = "/var/lib/porota-rc6-oci-custody/.bootstrap-r297-complete.stage"
ROOT_DIRECTORY_CREATE = (
    "/usr/local/libexec",
    "/usr/local/libexec/porota-rc6-custody",
    "/var/lib/porota-rc6-oci-custody",
)
ROOT_PARENTS_EXISTING = (
    "/", "/usr", "/usr/local", "/usr/local/sbin",
    "/var", "/var/lib",
)
HOST_ID_COMMAND = (
    "/usr/bin/curl -fsS --connect-timeout 3 --max-time 5 "
    "http://169.254.169.254/metadata/v1/id"
)
HOST_REGION_COMMAND = (
    "/usr/bin/curl -fsS --connect-timeout 3 --max-time 5 "
    "http://169.254.169.254/metadata/v1/region"
)
ROOT_STAGE_TARGETS = (
    ("cli", "/usr/local/sbin/.porota-rc6-host-installer.r297-stage",
     "/usr/local/sbin/porota-rc6-host-installer", 0o555),
    ("contract", "/usr/local/libexec/porota-rc6-custody/.root-installer-contract.r297-stage",
     "/usr/local/libexec/porota-rc6-custody/rc6_host_rootowned_installer_contract.py", 0o400),
    ("pin", "/var/lib/porota-rc6-oci-custody/.approved-manifest-pin.r297-stage",
     "/var/lib/porota-rc6-oci-custody/installer-approved-manifest.sha256", 0o400),
)
# Physical root commands intentionally DO NOT live in this guard.  A later
# reviewed implementation must solve independent trust pinning, nofollow,
# atomic fsync publication, stale/replay protection, and bounded host scope
# BEFORE exposing any executable bootstrap entrypoint.
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_DECIMAL = re.compile(r"[1-9][0-9]{0,19}\Z")
_DO_REGION = re.compile(r"[a-z]{2,12}[0-9]{1,2}\Z")
_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}\Z")
_USER = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,30}\Z")


class BootstrapHold(RuntimeError):
    exit_code = 79

    def __init__(self, reason):
        self.reason = reason if re.fullmatch(r"[A-Z][A-Z0-9_]{0,70}", reason) else "UNVERIFIED"
        super().__init__("RC6_R295_HOLD79:" + self.reason)


def _require(ok, reason):
    if not ok:
        raise BootstrapHold(reason)


@dataclass(frozen=True)
class BootstrapContext:
    main_sha: str
    owner_comment_id: int
    droplet_id: str
    region: str
    host: str
    user: str
    host_control_verified: bool = False


@dataclass(frozen=True)
class RootStageInstall:
    role: str
    stage: str
    target: str
    mode: int
    bytes: bytes
    sha256: str


def validate_context(env):
    """Check all authority selectors before network and certainly before sudo."""
    _require(type(env) is dict or hasattr(env, "get"), "CONTEXT_UNVERIFIED")
    _require(env.get("GITHUB_EVENT_NAME") == "workflow_dispatch" and
             env.get("GITHUB_REF") == "refs/heads/main" and
             env.get("GITHUB_ACTOR") == OWNER, "TRUSTED_MAIN_OWNER_REQUIRED")
    sha = env.get("GITHUB_SHA")
    _require(type(sha) is str and _HEX40.fullmatch(sha), "MAIN_SHA_UNVERIFIED")
    comment_raw = env.get("RC6_R295_OWNER_COMMENT_ID")
    _require(type(comment_raw) is str and _DECIMAL.fullmatch(comment_raw),
             "OWNER_GO_COMMENT_ID_UNVERIFIED")
    droplet = env.get("RC6_EXPECTED_DO_ID")
    region = env.get("RC6_EXPECTED_DO_REGION")
    _require(type(droplet) is str and re.fullmatch(r"[1-9][0-9]{5,19}", droplet),
             "DROPLET_TRUSTED_PIN_UNVERIFIED")
    _require(type(region) is str and _DO_REGION.fullmatch(region),
             "REGION_TRUSTED_PIN_UNVERIFIED")
    host = env.get("DO_HOST")
    user = env.get("DO_USER")
    _require(type(host) is str and _HOST.fullmatch(host) and
             type(user) is str and _USER.fullmatch(user),
             "SSH_SELECTOR_UNVERIFIED")
    for name in ("DO_SSH_KEY", "DO_KNOWN_HOSTS", "GITHUB_TOKEN"):
        value = env.get(name)
        _require(type(value) is str and 0 < len(value) < 32768 and
                 "\x00" not in value and "\r" not in value,
                 "PROTECTED_SECRET_UNVERIFIED")
    _require(re.fullmatch(r"[A-Za-z0-9_-]{10,4096}", env["GITHUB_TOKEN"]) is not None,
             "GITHUB_TOKEN_HEADER_UNVERIFIED")
    return BootstrapContext(main_sha=sha, owner_comment_id=int(comment_raw),
                            droplet_id=droplet, region=region, host=host, user=user)


def _strict_json(raw, *, limit=4096):
    _require(type(raw) is bytes and 0 < len(raw) <= limit, "GO_RECEIPT_SIZE")

    def _unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, "GO_RECEIPT_DUPLICATE_KEY")
            result[key] = value
        return result

    try:
        return json.loads(raw.decode("utf-8", "strict"),
                          object_pairs_hook=_unique_pairs,
                          parse_constant=lambda _v: _require(False, "GO_RECEIPT_NONFINITE"))
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise BootstrapHold("GO_RECEIPT_JSON_INVALID") from exc


def _utc_timestamp(raw):
    _require(type(raw) is str and re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", raw),
        "GO_RECEIPT_CLOCK_INVALID")
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BootstrapHold("GO_RECEIPT_CLOCK_INVALID") from exc


def verify_owner_go(comment, main_sha, expected_comment_id, *, now=None):
    """Operator-approved exact reviewed main SHA; bootstrap files only."""
    _require(type(main_sha) is str and _HEX40.fullmatch(main_sha) and
             type(expected_comment_id) is int and expected_comment_id > 0,
             "EXPECTED_RECEIPT_IDENTITY")
    _require(type(comment) is dict and comment.get("id") == expected_comment_id and
             type(comment.get("user")) is dict and
             comment["user"].get("login") == OWNER and
             comment.get("author_association") == "OWNER" and
             comment.get("issue_url") ==
             f"https://api.github.com/repos/{REPOSITORY}/issues/{ISSUE}" and
             type(comment.get("created_at")) is str and
             comment.get("created_at") == comment.get("updated_at"),
             "OWNER_GO_AUTHORITY_UNVERIFIED")
    body = comment.get("body")
    _require(type(body) is str and body.startswith(OWNER_GO_MARKER) and
             len(body) <= 4096, "OWNER_GO_BODY_UNVERIFIED")
    actual = _strict_json(body[len(OWNER_GO_MARKER):].encode("utf-8"))
    expected = {
        "operation": "TRUSTED_MAIN_ROOT_BOOTSTRAP_FILES_ONLY",
        "issue": ISSUE, "trusted_main_sha": main_sha,
        "installer_cli_sha256": TRUSTED_INSTALLER_CLI_SHA256,
        "installer_contract_sha256": TRUSTED_INSTALLER_CONTRACT_SHA256,
        "paper_mode": "PRODUCTION_PAPER", "real_orders_allowed": False,
        "host_control_verified": False,
    }
    _require(type(actual) is dict and
             set(actual) == set(expected) | {"approved_manifest_sha256", "expires_at"} and
             type(actual.get("approved_manifest_sha256")) is str and
             _HEX64.fullmatch(actual["approved_manifest_sha256"]) and
             type(actual.get("expires_at")) is str and
             all(actual.get(k) == value for k, value in expected.items()) and
             all(type(actual[key]) is type(expected[key]) for key in expected),
             "OWNER_GO_SCOPE_OR_SHA_MISMATCH")
    if now is None:
        now = datetime.now(timezone.utc)
    _require(type(now) is datetime and now.tzinfo is not None,
             "GO_RECEIPT_CLOCK_INVALID")
    issued = _utc_timestamp(comment["created_at"])
    expiry = _utc_timestamp(actual["expires_at"])
    _require(issued - timedelta(minutes=5) <= now < expiry <= issued + timedelta(hours=2),
             "OWNER_GO_EXPIRED_OR_FUTURE")
    return actual


def fetch_github_https_bytes(path, token):
    """Only a fixed api.github.com issue-comment GET; redirects are rejected."""
    allowed_owner_comment = (type(path) is str and path.startswith(
        f"/repos/{REPOSITORY}/issues/comments/") and
        _DECIMAL.fullmatch(path.rsplit("/", 1)[-1]))
    _require((allowed_owner_comment or
             path == f"/repos/{REPOSITORY}/git/ref/heads/main") and
        type(token) is str and re.fullmatch(r"[A-Za-z0-9_-]{10,4096}", token),
        "GITHUB_RECEIPT_SELECTOR_UNVERIFIED")
    connection = None
    try:
        connection = http.client.HTTPSConnection("api.github.com", timeout=8)
        connection.request("GET", path, headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Porota-RC6-R297-Owner-GO-Verify",
        })
        response = connection.getresponse()
        _require(response.status == 200 and
                 response.getheader("Content-Type", "").split(";", 1)[0] ==
                 "application/json", "GITHUB_OWNER_GO_RESPONSE_UNVERIFIED")
        data = response.read(16385)
        _require(0 < len(data) <= 16384, "GITHUB_RECEIPT_SIZE")
        return data
    except (OSError, ValueError, TimeoutError, http.client.HTTPException) as exc:
        raise BootstrapHold("GITHUB_RECEIPT_TRANSPORT_UNVERIFIED") from exc
    finally:
        if connection is not None:
            connection.close()


def fetch_and_verify_owner_go(context, *, token, fetch_bytes=None, now=None):
    _require(type(context) is BootstrapContext, "CONTEXT_UNVERIFIED")
    if fetch_bytes is None:
        fetch_bytes = fetch_github_https_bytes
    path = (f"/repos/{REPOSITORY}/issues/comments/" +
            str(context.owner_comment_id))
    _require(type(token) is str and re.fullmatch(r"[A-Za-z0-9_-]{10,4096}", token),
             "GITHUB_TOKEN_HEADER_UNVERIFIED")
    raw = fetch_bytes(path, token)
    _require(type(raw) is bytes and 0 < len(raw) <= 16384,
             "GITHUB_RECEIPT_SIZE")
    doc = _strict_json(raw, limit=16384)
    return verify_owner_go(doc, context.main_sha, context.owner_comment_id, now=now)


def verify_live_main_head(context, *, token, fetch_bytes=None):
    """Independent live branch-ref check, not a self-asserted GITHUB_SHA."""
    _require(type(context) is BootstrapContext, "CONTEXT_UNVERIFIED")
    if fetch_bytes is None:
        fetch_bytes = fetch_github_https_bytes
    path = f"/repos/{REPOSITORY}/git/ref/heads/main"
    doc = _strict_json(fetch_bytes(path, token), limit=16384)
    _require(type(doc) is dict and doc.get("ref") == "refs/heads/main" and
             type(doc.get("object")) is dict and
             doc["object"].get("type") == "commit" and
             doc["object"].get("sha") == context.main_sha,
             "MAIN_SHA_DRIFT")
    return context.main_sha


def approved_manifest_pin_bytes(go):
    """The external owner-receipt digest, NEVER a bundle-derived self-pin."""
    _require(type(go) is dict and type(go.get("approved_manifest_sha256")) is str and
             _HEX64.fullmatch(go["approved_manifest_sha256"]),
             "INDEPENDENT_MANIFEST_PIN_REQUIRED")
    return (go["approved_manifest_sha256"] + "\n").encode("ascii")


def build_root_bootstrap_plan(go, sources):
    """Frozen, fully pinned bytes; never re-open user-writable source on sudo."""
    _require(type(sources) is dict and set(sources) == {"cli", "contract"} and
             all(type(sources[name]) is bytes for name in ("cli", "contract")),
             "ROOT_SNAPSHOT_SCOPE_UNVERIFIED")
    _require(hashlib.sha256(sources["cli"]).hexdigest() ==
             TRUSTED_INSTALLER_CLI_SHA256 and
             hashlib.sha256(sources["contract"]).hexdigest() ==
             TRUSTED_INSTALLER_CONTRACT_SHA256,
             "ROOT_SOURCE_REVIEW_SHA_UNVERIFIED")
    pin = approved_manifest_pin_bytes(go)
    source = {"cli": sources["cli"], "contract": sources["contract"], "pin": pin}
    return tuple(RootStageInstall(role=role, stage=stage, target=target,
                                  mode=mode, bytes=source[role],
                                  sha256=hashlib.sha256(source[role]).hexdigest())
                 for role, stage, target, mode in ROOT_STAGE_TARGETS)


def reviewed_remote_root_commands():
    """A finite OS binary allowlist; never sudo Python or source from checkout."""
    paths = (*ROOT_PARENTS_EXISTING, *ROOT_DIRECTORY_CREATE, ROOT_LOCK_DIR,
             ROOT_RECEIPT, ROOT_RECEIPT_STAGE,
             *(p for _, stage, target, _ in ROOT_STAGE_TARGETS for p in (stage, target)))
    commands = set()
    for path in paths:
        for verb in ("stat -c '%u|%a|%h|%F'", "test -e", "test -L"):
            sep = " --" if verb.startswith("stat ") else ""
            commands.add(f"/usr/bin/sudo -n /usr/bin/{verb}{sep} {path}")
    for path in ROOT_DIRECTORY_CREATE:
        mode = "0755" if path == "/usr/local/libexec" else "0700"
        commands.add(f"/usr/bin/sudo -n /usr/bin/mkdir -m {mode} -- {path}")
        commands.add(f"/usr/bin/sudo -n /usr/bin/sync -- {path}")
    commands.add(f"/usr/bin/sudo -n /usr/bin/mkdir -m 0700 -- {ROOT_LOCK_DIR}")
    for _, stage, target, mode in ROOT_STAGE_TARGETS:
        commands.add(f"/usr/bin/sudo -n /usr/bin/install -m {mode:04o} -o 0 -g 0 "
                     f"/dev/stdin {stage}")
        commands.add(f"/usr/bin/sudo -n /usr/bin/sha256sum -- {stage}")
        commands.add(f"/usr/bin/sudo -n /usr/bin/sha256sum -- {target}")
        commands.add(f"/usr/bin/sudo -n /usr/bin/sync -- {stage}")
        commands.add(f"/usr/bin/sudo -n /usr/bin/sync -- {target}")
        commands.add(f"/usr/bin/sudo -n /usr/bin/sync -- {str(Path(target).parent)}")
        # O_EXCL semantics: GNU ln without -f refuses any existing target.
        # Stage and target share one protected root-owned parent/filesystem.
        commands.add(f"/usr/bin/sudo -n /usr/bin/ln -T -- {stage} {target}")
        commands.add(f"/usr/bin/sudo -n /usr/bin/rm -- {stage}")
    commands.add(f"/usr/bin/sudo -n /usr/bin/install -m 0400 -o 0 -g 0 "
                 f"/dev/stdin {ROOT_RECEIPT_STAGE}")
    commands.add(f"/usr/bin/sudo -n /usr/bin/sha256sum -- {ROOT_RECEIPT_STAGE}")
    commands.add(f"/usr/bin/sudo -n /usr/bin/sync -- {ROOT_RECEIPT_STAGE}")
    commands.add(f"/usr/bin/sudo -n /usr/bin/ln -T -- {ROOT_RECEIPT_STAGE} {ROOT_RECEIPT}")
    commands.add(f"/usr/bin/sudo -n /usr/bin/rm -- {ROOT_RECEIPT_STAGE}")
    commands.add(f"/usr/bin/sudo -n /usr/bin/sync -- "
                 "/var/lib/porota-rc6-oci-custody")
    commands.add("/usr/bin/sudo -n /usr/bin/sync -- /var/lib")
    commands.add(f"/usr/bin/sudo -n /usr/bin/sha256sum -- {ROOT_RECEIPT}")
    commands.add(f"/usr/bin/sudo -n /usr/bin/sync -- {ROOT_RECEIPT}")
    return frozenset(commands)


def _host_command(host, command, *, stdin=None, statuses=(0,), cap=256):
    """Fixed call gate; a transport cannot receive arbitrary root commands."""
    _require(command in reviewed_remote_root_commands() or
             command in (HOST_ID_COMMAND, HOST_REGION_COMMAND),
             "HOST_COMMAND_NOT_REVIEWED")
    _require(stdin is None or type(stdin) is bytes and len(stdin) <= 1024 * 1024,
             "HOST_STDIN_INVALID")
    try:
        result = host.call(command, stdin=stdin)
    except Exception as exc:
        raise BootstrapHold("HOST_REMOTE_COMMAND_UNVERIFIED") from exc
    _require(type(result) is tuple and len(result) == 2 and
             type(result[0]) is int and result[0] in statuses and
             type(result[1]) is bytes and len(result[1]) <= cap,
             "HOST_REMOTE_STATUS_OR_OUTPUT_UNVERIFIED")
    return result[0], result[1]


def _root_exists(host, path):
    _require(path in (*ROOT_PARENTS_EXISTING, *ROOT_DIRECTORY_CREATE,
                       ROOT_LOCK_DIR, ROOT_RECEIPT, ROOT_RECEIPT_STAGE,
                       *(p for _, stage, target, _ in ROOT_STAGE_TARGETS
                         for p in (stage, target))),
             "ROOT_PATH_OUT_OF_SCOPE")
    rc_symlink, _ = _host_command(host, f"/usr/bin/sudo -n /usr/bin/test -L {path}",
                                  statuses=(0, 1))
    _require(rc_symlink == 1, "ROOT_SYMLINK_FORBIDDEN")
    rc, _ = _host_command(host, f"/usr/bin/sudo -n /usr/bin/test -e {path}",
                          statuses=(0, 1))
    return rc == 0


def _root_stat(host, path, *, is_dir, mode=None):
    _rc, raw = _host_command(host,
        f"/usr/bin/sudo -n /usr/bin/stat -c '%u|%a|%h|%F' -- {path}", cap=128)
    validate_root_stat(raw, is_dir=is_dir, mode=mode)


def _root_digest(host, path, expected):
    _require(type(expected) is str and _HEX64.fullmatch(expected),
             "ROOT_DIGEST_EXPECTED_INVALID")
    _rc, raw = _host_command(host, f"/usr/bin/sudo -n /usr/bin/sha256sum -- {path}", cap=256)
    _require(raw == (expected + "  " + path + "\n").encode("ascii"),
             "ROOT_PUBLISHED_BYTES_HASH_MISMATCH")


def _root_sync(host, path):
    _host_command(host, f"/usr/bin/sudo -n /usr/bin/sync -- {path}", cap=0)


def _install_stage(host, item):
    _require(type(item) is RootStageInstall and not _root_exists(host, item.stage),
             "ROOT_STAGE_COLLISION")
    _host_command(host, f"/usr/bin/sudo -n /usr/bin/install -m {item.mode:04o} -o 0 -g 0 "
                        f"/dev/stdin {item.stage}", stdin=item.bytes, cap=0)
    _root_stat(host, item.stage, is_dir=False, mode=item.mode)
    _root_digest(host, item.stage, item.sha256)
    _root_sync(host, item.stage)


def _publish_stage(host, item):
    # GNU ln, without -f, creates a target name atomically or fails. Both
    # files live in the SAME verified root-owned directory / same filesystem.
    _require(not _root_exists(host, item.target), "ROOT_TARGET_COLLISION")
    _host_command(host, f"/usr/bin/sudo -n /usr/bin/ln -T -- {item.stage} {item.target}", cap=0)
    _host_command(host, f"/usr/bin/sudo -n /usr/bin/rm -- {item.stage}", cap=0)
    _root_sync(host, item.target)
    _root_sync(host, str(Path(item.target).parent))
    _root_stat(host, item.target, is_dir=False, mode=item.mode)
    _root_digest(host, item.target, item.sha256)


def _bootstrap_receipt_bytes(context, go):
    _require(type(context) is BootstrapContext and type(go) is dict,
             "BOOTSTRAP_RECEIPT_CONTEXT_INVALID")
    doc = {
        "schema": "RC6_R297_ROOT_BOOTSTRAP_FILES_ONLY_V1",
        "status": "INSTALLED_FILES_ONLY_NOT_HOST_GO",
        "trusted_main_sha": context.main_sha,
        "owner_go_comment_id": context.owner_comment_id,
        "approved_manifest_sha256": go["approved_manifest_sha256"],
        "installer_cli_sha256": TRUSTED_INSTALLER_CLI_SHA256,
        "installer_contract_sha256": TRUSTED_INSTALLER_CONTRACT_SHA256,
        "paper_mode": "PRODUCTION_PAPER",
        "real_orders_allowed": False,
        "host_control_verified": False,
    }
    return (json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n").encode()


def execute_one_shot_root_bootstrap(host, context, owner_go, sources, *,
                                    recheck_authority):
    """Bounded root files-only transaction via a reviewed transport.

    This function has NO CLI entrypoint and cannot run from the R297 workflow.
    Actual physical activation needs independent QA, trusted SSH transport,
    protected DO pins, owner-GO and a new change controlling its invocation.
    A durable, root-owned lock is created before the first write. Any crash
    leaves the lock to force fresh human-reviewed fix-forward: never auto retry.
    """
    _require(type(context) is BootstrapContext and type(owner_go) is dict and
             owner_go.get("host_control_verified") is False and
             callable(recheck_authority), "ROOT_AUTHORITY_UNVERIFIED")
    plan = build_root_bootstrap_plan(owner_go, sources)

    def check_authority():
        latest_go, live_main_sha = recheck_authority()
        _require(latest_go == owner_go and live_main_sha == context.main_sha,
                 "ROOT_OWNER_GO_OR_MAIN_DRIFT")

    check_authority()
    _rc, droplet = _host_command(host, HOST_ID_COMMAND, cap=32)
    _rc, region = _host_command(host, HOST_REGION_COMMAND, cap=32)
    verify_host_identity(droplet, region, context)
    # Verify ALL existing parent ancestors; symlink/writable/foreign root
    # paths cannot be trusted. Missing generic ancestor => HOLD, not mkdir.
    for parent in ROOT_PARENTS_EXISTING:
        _require(_root_exists(host, parent), "ROOT_PARENT_MISSING")
        _root_stat(host, parent, is_dir=True)
    _require(not _root_exists(host, ROOT_LOCK_DIR) and
             not _root_exists(host, ROOT_RECEIPT), "ROOT_ONE_SHOT_REPLAY")
    for item in plan:
        _require(not _root_exists(host, item.stage) and
                 not _root_exists(host, item.target), "ROOT_TARGET_OR_STAGE_COLLISION")
    _require(not _root_exists(host, ROOT_RECEIPT_STAGE),
             "ROOT_RECEIPT_STAGE_COLLISION")
    for directory in ROOT_DIRECTORY_CREATE:
        if _root_exists(host, directory):
            _root_stat(host, directory, is_dir=True)

    # First mutation is durable one-shot reservation. No root code is
    # executed, only fixed system commands with pinned operator-approved data.
    _host_command(host, f"/usr/bin/sudo -n /usr/bin/mkdir -m 0700 -- {ROOT_LOCK_DIR}", cap=0)
    _root_stat(host, ROOT_LOCK_DIR, is_dir=True, mode=0o700)
    _root_sync(host, "/var/lib")
    for directory in ROOT_DIRECTORY_CREATE:
        if not _root_exists(host, directory):
            mode = "0755" if directory == "/usr/local/libexec" else "0700"
            _host_command(host,
                          f"/usr/bin/sudo -n /usr/bin/mkdir -m {mode} -- {directory}", cap=0)
        _root_stat(host, directory, is_dir=True)
        _root_sync(host, directory)

    for item in plan:
        _install_stage(host, item)
    check_authority()  # no byte publication while main/owner-GO drifted
    for item in plan[:2]:
        _publish_stage(host, item)
    check_authority()  # external digest approval checked AGAIN before pin
    _publish_stage(host, plan[2])  # independent manifest SHA pin LAST
    check_authority()
    receipt_raw = _bootstrap_receipt_bytes(context, owner_go)
    receipt_item = RootStageInstall(
        role="receipt", stage=ROOT_RECEIPT_STAGE, target=ROOT_RECEIPT,
        mode=0o400, bytes=receipt_raw,
        sha256=hashlib.sha256(receipt_raw).hexdigest())
    _install_stage(host, receipt_item)
    _publish_stage(host, receipt_item)
    return {"schema": "RC6_R297_ROOT_BOOTSTRAP_FILES_ONLY_V1",
            "status": "INSTALLED_FILES_ONLY_NOT_HOST_GO",
            "trusted_main_sha": context.main_sha,
            "owner_go_comment_id": context.owner_comment_id,
            "host_control_verified": False}


def validate_root_stat(raw, *, is_dir, mode=None):
    """Read GNU stat -c %u|%a|%h|%F without dereferencing a symlink."""
    _require(type(raw) is bytes and len(raw) <= 128, "ROOT_STAT_SIZE")
    try:
        user, octal, links, kind = raw.decode("ascii", "strict").strip("\n").split("|")
        bits = int(octal, 8)
        nlinks = int(links)
    except (ValueError, UnicodeError) as exc:
        raise BootstrapHold("ROOT_STAT_INVALID") from exc
    _require(user == "0" and 0 <= bits <= 0o7777 and nlinks >= 1 and
             not bits & 0o022, "ROOT_OWNER_OR_MODE_UNVERIFIED")
    _require(kind == ("directory" if is_dir else "regular file") and
             (is_dir or nlinks == 1) and (mode is None or bits == mode),
             "ROOT_INODE_KIND_LINKS_OR_MODE_UNVERIFIED")


def safe_osc52_status(report):
    """Opt-in clipboard packet limited to sanitized non-sensitive status only."""
    _require(type(report) is str and 1 <= len(report) <= 160 and
             re.fullmatch(r"RC6_R297=(?:HOLD79|NO_GO|FILES_INSTALLED_ONLY)"
                          r"(?:\|(?:id|region|sudo|cli|pin)=(?:MATCH|YES|NO|ABSENT|PRESENT|UNVERIFIED))*",
                          report), "OSC52_SENSITIVE_OR_INVALID_REPORT")
    return "\x1b]52;c;" + base64.b64encode(report.encode("ascii")).decode("ascii") + "\x07"


def _read_reviewed_file(path, max_size):
    _require(isinstance(path, Path) and max_size > 0, "SOURCE_PATH_SHAPE")
    nofollow = getattr(os, "O_NOFOLLOW", None)
    _require(type(nofollow) is int and nofollow != 0, "OS_NOFOLLOW_REQUIRED")
    try:
        fd = os.open(path, os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0))
        try:
            before = os.fstat(fd)
            _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
                     0 < before.st_size <= max_size, "SOURCE_REGULAR_FILE_REQUIRED")
            raw = os.read(fd, max_size + 1)
            after = os.fstat(fd)
            _require(len(raw) == before.st_size and
                     before.st_ino == after.st_ino and
                     before.st_size == after.st_size, "SOURCE_CHANGED")
            return raw
        finally:
            os.close(fd)
    except OSError as exc:
        raise BootstrapHold("SOURCE_FILE_UNVERIFIED") from exc


def verify_reviewed_sources(root):
    """Only the two existing R280 reviewed byte blobs, not a live bundle."""
    _require(isinstance(root, Path) and root.is_dir(), "SOURCE_ROOT_UNVERIFIED")
    files = {role: _read_reviewed_file(root / "scripts" / source, 1024 * 1024)
             for role, source in SOURCE_FILES.items()}
    _require(hashlib.sha256(files["cli"]).hexdigest() ==
             TRUSTED_INSTALLER_CLI_SHA256 and
             hashlib.sha256(files["contract"]).hexdigest() ==
             TRUSTED_INSTALLER_CONTRACT_SHA256,
             "REVIEWED_SOURCE_SHA_MISMATCH")
    _require(files["cli"].startswith(b"#!/usr/bin/python3 -I\n") and
             ("TRUSTED_CONTRACT_SHA256 = \"" +
              TRUSTED_INSTALLER_CONTRACT_SHA256 + "\"").encode() in files["cli"],
             "ROOT_CLI_SOURCE_CONTRACT_PIN_MISMATCH")
    return files


def _one_ascii_line(raw):
    _require(type(raw) is bytes and 0 < len(raw) <= 32, "HOST_IDENTITY_SIZE")
    try:
        value = raw.decode("ascii", "strict")
    except UnicodeError as exc:
        raise BootstrapHold("HOST_IDENTITY_ENCODING") from exc
    if value.endswith("\n"):
        value = value[:-1]
    _require("\r" not in value and "\n" not in value and "\x00" not in value,
             "HOST_IDENTITY_MULTILINE")
    return value


def verify_host_identity(observed_id, observed_region, context):
    _require(type(context) is BootstrapContext and
             _one_ascii_line(observed_id) == context.droplet_id and
             _one_ascii_line(observed_region) == context.region,
             "DROPLET_IDENTITY_UNVERIFIED")


class StrictSSHHost:
    """One-shot trusted-runner SSH transport with a literal remote allowlist.

    No commands are run merely by constructing this class. Only an explicitly
    authorized --execute-approved-bootstrap caller can enter the context.
    """
    def __init__(self, context, env):
        _require(type(context) is BootstrapContext and type(env) is dict or
                 type(context) is BootstrapContext and hasattr(env, "get"),
                 "SSH_TRUSTED_CONTEXT_REQUIRED")
        _require(type(env.get("DO_SSH_KEY")) is str and
                 type(env.get("DO_KNOWN_HOSTS")) is str,
                 "SSH_PROTECTED_SECRETS_REQUIRED")
        self.context = context
        self.key = env["DO_SSH_KEY"].encode("utf-8") + b"\n"
        self.known = env["DO_KNOWN_HOSTS"].encode("utf-8") + b"\n"
        self.temp = None
        self.ssh_args = None

    def __enter__(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rc6-r297-ssh-")
        folder = Path(self.temp.name)
        key_path, known_path = folder / "id_ed25519", folder / "known_hosts"
        for path, raw in ((key_path, self.key), (known_path, self.known)):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                os.fchmod(fd, 0o600)
                start = 0
                while start < len(raw):
                    count = os.write(fd, raw[start:])
                    _require(count > 0, "SSH_SECRET_TEMP_WRITE_SHORT")
                    start += count
                os.fsync(fd)
            finally:
                os.close(fd)
        self.ssh_args = [
            "/usr/bin/ssh", "-T", "-F", "/dev/null", "-i", str(key_path),
            "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=" + str(known_path),
            "-o", "GlobalKnownHostsFile=/dev/null", "-o", "ProxyCommand=none",
            "-o", "ProxyJump=none", "-o", "ControlMaster=no",
            "-o", "ClearAllForwardings=yes", "-o", "UpdateHostKeys=no",
            "-o", "PasswordAuthentication=no", "-o", "KbdInteractiveAuthentication=no",
            "-o", "ConnectTimeout=8", "-o", "ConnectionAttempts=1",
            "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2",
            "-o", "LogLevel=ERROR",
            f"{self.context.user}@{self.context.host}",
        ]
        return self

    def call(self, command, stdin=None):
        _require(self.ssh_args is not None and
                 (command in reviewed_remote_root_commands() or
                  command in (HOST_ID_COMMAND, HOST_REGION_COMMAND)),
                 "SSH_COMMAND_NOT_REVIEWED")
        _require(stdin is None or type(stdin) is bytes and len(stdin) <= 1024 * 1024,
                 "SSH_STDIN_INVALID")
        try:
            finished = subprocess.run(self.ssh_args + [command],
                                      input=b"" if stdin is None else stdin,
                                      capture_output=True, timeout=22, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BootstrapHold("SSH_TRANSPORT_UNVERIFIED") from exc
        _require(type(finished.stdout) is bytes and len(finished.stdout) <= 4096 and
                 type(finished.stderr) is bytes and len(finished.stderr) <= 4096,
                 "SSH_OUTPUT_SIZE_UNVERIFIED")
        return finished.returncode, finished.stdout

    def __exit__(self, *_exc):
        try:
            if self.temp is not None:
                self.temp.cleanup()  # runner-only SSH credentials, never host data
        finally:
            self.ssh_args = None
            self.key = b""
            self.known = b""



def main(argv=None, *, env=None, owner_getter=None, main_ref_getter=None,
         host_factory=None):
    """Two distinctly labeled paths: offhost GO evidence or root files-only.

    Root mode can run only with issue OWNER-GO + exact live main SHA, source
    SHA pins, DO metadata, strict SSH and a fresh root-only one-shot lock.
    The shipped R297 workflow calls only --check-offhost. Physical root mode
    remains un-dispatched/unmerged until independent QA of code and transport.
    """
    if argv is None:
        argv = sys.argv[1:]
    if env is None:
        env = os.environ
    try:
        _require(argv in (["--check-offhost"], ["--execute-approved-bootstrap"]),
                 "FIXED_BOOTSTRAP_VERB_ONLY")
        context = validate_context(env)
        sources = verify_reviewed_sources(Path(__file__).resolve().parents[1])

        def current_main():
            if main_ref_getter is None:
                return verify_live_main_head(context, token=env["GITHUB_TOKEN"])
            return main_ref_getter(context)

        def current_go():
            if owner_getter is None:
                return fetch_and_verify_owner_go(context, token=env["GITHUB_TOKEN"])
            return owner_getter(context)

        _require(current_main() == context.main_sha, "MAIN_SHA_DRIFT")
        go = current_go()
        _require(type(go) is dict and go.get("host_control_verified") is False,
                 "OFFHOST_OWNER_GO_UNVERIFIED")
        approved_manifest_pin_bytes(go)
        if argv == ["--check-offhost"]:
            print("RC6_R297_BOOTSTRAP=OWNER_GO_OFFHOST_VERIFIED|"
                  "host_control_verified=NO_VERIFICADO|deploy_authorized=NO")
            return 0
        if host_factory is None:
            host_factory = StrictSSHHost
        with host_factory(context, env) as host:
            report = execute_one_shot_root_bootstrap(
                host, context, go, sources,
                recheck_authority=lambda: (current_go(), current_main()))
        _require(report.get("status") == "INSTALLED_FILES_ONLY_NOT_HOST_GO" and
                 report.get("host_control_verified") is False,
                 "ROOT_BOOTSTRAP_RECEIPT_INVALID")
        print("RC6_R297_BOOTSTRAP=FILES_INSTALLED_ONLY_NOT_HOST_GO|"
              "host_control_verified=NO_VERIFICADO|deploy_authorized=NO")
        return 0
    except BootstrapHold as exc:
        print("RC6_R295_BOOTSTRAP=HOLD79|reason=" + exc.reason +
              "|host_control_verified=NO_VERIFICADO|deploy_authorized=NO")
        return 79
    except Exception:
        print("RC6_R295_BOOTSTRAP=HOLD79|reason=UNHANDLED_FAIL_CLOSED|"
              "host_control_verified=NO_VERIFICADO|deploy_authorized=NO")
        return 79


if __name__ == "__main__":
    raise SystemExit(main())
