"""R320 reconstruction of R305, OFFHOST-only root manifest pin transition.

This is an exact, bounded command/validation library, not a deploy workflow.
Only the independent Actions controller may supply authenticated GitHub owner
comments, witnessed artifacts and a protected DigitalOcean identity. The
operator's existing R297 bootstrap and its historical RELEASE-D pin must not
be confused with the separate five-role ROOT_INSTALL manifest.

No direct SSH session, shell, Docker, systemd, DB, real order or PPI Watch
operation is present. A completed pin switch is FILES_ONLY, never HOST GO.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType

from scripts import rc6_host_rootowned_installer_contract as r280
from scripts import rc6_r302_bootstrap_receipt_preflight as r302


REPOSITORY = "mbalbo2023/Porota-trading"
OWNER = "mbalbo2023"
ISSUE = 1093
OWNER_GO_MARKER = "RC6_R305_ROOT_PIN_GO_V1="
HISTORICAL_D_PIN = r302.APPROVED_R301_D_MANIFEST_SHA256
R301_RECEIPT_SHA256 = hashlib.sha256(r302._expected_receipt_bytes()).hexdigest()
ROOT_PIN = r302.ROOT_PIN
R301_RECEIPT = r302.ROOT_RECEIPT
R301_LOCK = r302.ROOT_LOCK
ROOT_LOCK = "/var/lib/.porota-rc6-r305-pin-transition-reserved"
ROOT_OLD_ARCHIVE = (
    "/var/lib/porota-rc6-oci-custody/installer-approved-manifest.r305-historical-D.sha256"
)
ROOT_STAGE = ROOT_LOCK + "/new-root-manifest-pin"
ROOT_TRANSITION_RECEIPT_STAGE = ROOT_LOCK + "/transition-receipt.stage"
ROOT_TRANSITION_RECEIPT = (
    "/var/lib/porota-rc6-oci-custody/root-manifest-pin-r305-transition.json"
)
ROOT_DIRECTORY = "/var/lib/porota-rc6-oci-custody"
_DIRECTORIES = (
    "/", "/var", "/var/lib", ROOT_DIRECTORY, R301_LOCK, ROOT_LOCK,
)
_FILES = (
    ROOT_PIN, R301_RECEIPT, ROOT_OLD_ARCHIVE, ROOT_STAGE,
    ROOT_TRANSITION_RECEIPT_STAGE, ROOT_TRANSITION_RECEIPT,
)
_PATHS = _DIRECTORIES + _FILES
HOST_ID_COMMAND = (
    "/usr/bin/curl -fsS --connect-timeout 3 --max-time 5 "
    "http://169.254.169.254/metadata/v1/id"
)
HOST_REGION_COMMAND = (
    "/usr/bin/curl -fsS --connect-timeout 3 --max-time 5 "
    "http://169.254.169.254/metadata/v1/region"
)
# The only installed elevated Python entry allowed through R309. This
# separate R280 operation is NOT called by R305 pin transition. The owner
# controller must first prove exact ROOT manifest, staged five roles and
# independent fresh GO. R280's installed CLI rechecks root inode/hash and
# hardcodes argv=['install-approved']; no checkout Python is permitted.
INSTALL_APPROVED_COMMAND = (
    "/usr/bin/sudo -n /usr/bin/python3 -I "
    "/usr/local/sbin/porota-rc6-host-installer install-approved"
)

# R321 source staging is a separate owner/GO state machine, not executed by
# this R305 pin switch. Exposing its finite command set lets the same already
# reviewed R309 SSH mux transport fixed-size <=8192-byte writes, without a
# second ad hoc remote execution client or an executable shell.
STAGE_LOCK = "/var/lib/.porota-rc6-r321-root-stage-reserved"
STAGE_INBOX = r280.INBOX_DIR
STAGE_LOCK_CREATE = f"/usr/bin/sudo -n /usr/bin/mkdir -m 0700 -- {STAGE_LOCK}"
STAGE_INBOX_CREATE = f"/usr/bin/sudo -n /usr/bin/mkdir -m 0700 -- {STAGE_INBOX}"
_STAGE_FMT = "'%u|%g|%a|%h|%F|%d|%i|%s'"
STAGE_LOCK_STAT = (
    f"/usr/bin/sudo -n /usr/bin/stat -c {_STAGE_FMT} -- {STAGE_LOCK}"
)
STAGE_INBOX_STAT = (
    f"/usr/bin/sudo -n /usr/bin/stat -c {_STAGE_FMT} -- {STAGE_INBOX}"
)
STAGE_LOCK_SYNC = f"/usr/bin/sudo -n /usr/bin/sync -- {STAGE_LOCK}"
STAGE_INBOX_SYNC = f"/usr/bin/sudo -n /usr/bin/sync -- {STAGE_INBOX}"
_STAGE_SOURCE_NAMES = MappingProxyType({
    "helper": "helper.bin",
    "custody": "custody.py",
    "profiles": "profiles.json",
    "approval": "approval.json",
    "unit_template": "critical-unit-template.service",
    "manifest": "manifest.json",
})

_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_DECIMAL = re.compile(r"[1-9][0-9]{0,19}\Z")
_DO_REGION = re.compile(r"[a-z]{2,12}[0-9]{1,2}\Z")
_MANIFEST_KEYS = frozenset((
    "schema", "candidate_sha", "candidate_tree", "paper_mode",
    "real_orders_allowed", "files",
))
_ROLES = frozenset(("helper", "custody", "profiles", "unit_template", "approval"))
_WITNESS_KEYS = frozenset((
    "schema", "run_id", "run_attempt", "trusted_main_sha",
    "trusted_main_tree", "manifest_schema", "manifest_sha256",
    "candidate_sha", "candidate_tree", "verification_result",
    "paper_mode", "real_orders_allowed",
))
_GO_BASE_KEYS = frozenset((
    "operation", "issue", "trusted_main_sha", "candidate_sha",
    "candidate_tree", "manifest_sha256", "witness_artifact_sha256",
    "witness_run_id", "witness_run_attempt", "paper_mode",
    "real_orders_allowed", "host_control_verified",
    "host_identity_source", "expires_at",
))


class PinHold(RuntimeError):
    """Stable safe HOLD79; no protected host identity or raw stderr appears."""

    exit_code = 79

    def __init__(self, reason):
        self.reason = (reason if type(reason) is str and
                       re.fullmatch(r"[A-Z][A-Z0-9_]{0,70}", reason)
                       else "UNVERIFIED")
        super().__init__("RC6_R305_PIN_HOLD79:" + self.reason)


def _require(condition, reason):
    if not condition:
        raise PinHold(reason)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _strict_json(raw, *, cap):
    _require(type(raw) is bytes and 0 < len(raw) <= cap, "JSON_SIZE")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, "JSON_DUPLICATE_KEY")
            result[key] = value
        return result

    def nonfinite(_value):
        raise PinHold("JSON_NONFINITE")

    try:
        doc = json.loads(raw.decode("utf-8", "strict"),
                         object_pairs_hook=unique, parse_constant=nonfinite)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise PinHold("JSON_INVALID") from None
    _require(type(doc) is dict, "JSON_OBJECT_REQUIRED")
    return doc


def _canonical(doc):
    try:
        return (json.dumps(doc, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise PinHold("JSON_CANONICALIZATION_INVALID") from None


@dataclass(frozen=True)
class PinContext:
    """Protected Actions/owner selectors; the values are not self-attestation."""

    main_sha: str
    main_tree: str
    candidate_sha: str
    candidate_tree: str
    manifest_sha256: str
    witness_artifact_sha256: str
    do_id: str = field(repr=False)
    do_region: str = field(repr=False)


def _validate_context(ctx):
    _require(type(ctx) is PinContext, "TRUSTED_CONTEXT_REQUIRED")
    _require(all(type(getattr(ctx, key)) is str and
                 _HEX40.fullmatch(getattr(ctx, key))
                 for key in ("main_sha", "main_tree",
                             "candidate_sha", "candidate_tree")) and
             all(type(getattr(ctx, key)) is str and
                 _HEX64.fullmatch(getattr(ctx, key))
                 for key in ("manifest_sha256", "witness_artifact_sha256")) and
             type(ctx.do_id) is str and
             re.fullmatch(r"[1-9][0-9]{5,19}", ctx.do_id) and
             type(ctx.do_region) is str and _DO_REGION.fullmatch(ctx.do_region),
             "TRUSTED_CONTEXT_SHAPE_INVALID")


def validate_root_manifest(raw, *, expected_digest):
    """Check externally pinned bytes; NEVER derive owner GO from this digest."""
    _require(type(expected_digest) is str and _HEX64.fullmatch(expected_digest),
             "ROOT_PIN_EXPECTED_DIGEST_REQUIRED")
    _require(type(raw) is bytes and 0 < len(raw) <= 8192, "ROOT_MANIFEST_SIZE")
    _require(_sha(raw) == expected_digest, "ROOT_MANIFEST_DIGEST_MISMATCH")
    doc = _strict_json(raw, cap=8192)
    _require(set(doc) == _MANIFEST_KEYS and
             doc["schema"] == "RC6_ROOT_INSTALL_MANIFEST_V1" and
             type(doc["candidate_sha"]) is str and
             _HEX40.fullmatch(doc["candidate_sha"]) and
             type(doc["candidate_tree"]) is str and
             _HEX40.fullmatch(doc["candidate_tree"]) and
             doc["paper_mode"] == "PRODUCTION_PAPER" and
             doc["real_orders_allowed"] is False and
             type(doc["files"]) is dict and set(doc["files"]) == _ROLES and
             all(type(value) is str and _HEX64.fullmatch(value)
                 for value in doc["files"].values()),
             "ROOT_MANIFEST_SEMANTICS_INVALID")
    _require(raw == _canonical(doc), "ROOT_MANIFEST_NOT_CANONICAL")
    return doc


def validate_independent_witness(raw, *, expected_digest, manifest, context):
    """Validate detached witness shape; controller MUST authenticate artifact.

    Comparing local bytes is not itself proof of GitHub Actions provenance:
    the trusted caller is required to verify run/job/artifact/commit through
    authenticated GitHub API independently from dispatch user inputs.
    """
    _validate_context(context)
    _require(type(expected_digest) is str and _HEX64.fullmatch(expected_digest),
             "WITNESS_EXPECTED_DIGEST_REQUIRED")
    _require(type(raw) is bytes and 0 < len(raw) <= 4096 and
             _sha(raw) == expected_digest ==
             context.witness_artifact_sha256, "WITNESS_DIGEST_MISMATCH")
    doc = _strict_json(raw, cap=4096)
    _require(type(manifest) is dict and
             set(manifest) == _MANIFEST_KEYS and
             set(doc) == _WITNESS_KEYS and
             doc["schema"] ==
             "RC6_R305_ROOT_MANIFEST_INDEPENDENT_DIGEST_WITNESS_V1" and
             doc["manifest_schema"] == "RC6_ROOT_INSTALL_MANIFEST_V1" and
             doc["verification_result"] == "PASS" and
             type(doc["run_id"]) is str and _DECIMAL.fullmatch(doc["run_id"]) and
             type(doc["run_attempt"]) is str and
             _DECIMAL.fullmatch(doc["run_attempt"]) and
             doc["trusted_main_sha"] == context.main_sha and
             doc["trusted_main_tree"] == context.main_tree and
             doc["manifest_sha256"] == context.manifest_sha256 and
             doc["candidate_sha"] == manifest["candidate_sha"] ==
             context.candidate_sha and
             doc["candidate_tree"] == manifest["candidate_tree"] ==
             context.candidate_tree and
             doc["paper_mode"] == "PRODUCTION_PAPER" and
             doc["real_orders_allowed"] is False,
             "WITNESS_SCOPE_OR_IDENTITY_MISMATCH")
    _require(raw == _canonical(doc), "WITNESS_NOT_CANONICAL")
    return doc


def _utc(value):
    _require(type(value) is str and re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value),
        "OWNER_GO_CLOCK_INVALID")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise PinHold("OWNER_GO_CLOCK_INVALID") from None


def verify_owner_go(comment, context, *, owner_go_id, operation, now,
                    original_go_comment=None):
    """Authenticate one immutable owner comment, scoped to ONE ROOT pin.

    GitHub API authentication and live main ref are independent operations
    performed by the trusted caller, never inferred from an input comment.
    RESUME requires a *new* GO and authentic original owner comment.
    """
    _validate_context(context)
    _require(operation in ("NEW", "RESUME") and
             type(owner_go_id) is int and owner_go_id > 0 and
             type(now) is datetime and now.tzinfo is not None,
             "OWNER_GO_CONTEXT_INVALID")
    _require(type(comment) is dict and comment.get("id") == owner_go_id and
             comment.get("issue_url") ==
             f"https://api.github.com/repos/{REPOSITORY}/issues/{ISSUE}" and
             type(comment.get("user")) is dict and
             comment["user"].get("login") == OWNER and
             comment.get("author_association") == "OWNER" and
             comment.get("created_at") == comment.get("updated_at") and
             type(comment.get("body")) is str and
             comment["body"].startswith(OWNER_GO_MARKER) and
             len(comment["body"]) <= 4096,
             "OWNER_GO_AUTHORITY_UNVERIFIED")
    body = comment["body"][len(OWNER_GO_MARKER):].encode("utf-8")
    approval = _strict_json(body, cap=4096)
    keys = _GO_BASE_KEYS | (
        {"original_owner_go_comment_id"} if operation == "RESUME" else set())
    _require(set(approval) == keys and
             approval["operation"] == "RC6_ROOT_PIN_" + operation and
             type(approval["issue"]) is int and approval["issue"] == ISSUE and
             approval["trusted_main_sha"] == context.main_sha and
             approval["candidate_sha"] == context.candidate_sha and
             approval["candidate_tree"] == context.candidate_tree and
             approval["manifest_sha256"] == context.manifest_sha256 and
             approval["witness_artifact_sha256"] ==
             context.witness_artifact_sha256 and
             type(approval["witness_run_id"]) is str and
             _DECIMAL.fullmatch(approval["witness_run_id"]) and
             type(approval["witness_run_attempt"]) is str and
             _DECIMAL.fullmatch(approval["witness_run_attempt"]) and
             approval["paper_mode"] == "PRODUCTION_PAPER" and
             approval["real_orders_allowed"] is False and
             approval["host_control_verified"] is False and
             approval["host_identity_source"] == "PROTECTED_DO_PINS",
             "OWNER_GO_SCOPE_OR_HASH_MISMATCH")
    issued, expiry = _utc(comment["created_at"]), _utc(approval["expires_at"])
    actual_now = now.astimezone(timezone.utc)
    _require(issued - timedelta(minutes=5) <= actual_now < expiry <=
             issued + timedelta(hours=2), "OWNER_GO_EXPIRED_OR_FUTURE")
    if operation == "RESUME":
        old_id = approval.get("original_owner_go_comment_id")
        _require(type(old_id) is int and 0 < old_id < owner_go_id and
                 type(original_go_comment) is dict and
                 original_go_comment.get("id") == old_id,
                 "ROOT_ORIGINAL_GO_REQUIRED")
        # A stale original is verified at its ISSUANCE, never used for writes.
        original_time = _utc(original_go_comment.get("created_at"))
        original = verify_owner_go(
            original_go_comment, context, owner_go_id=old_id,
            operation="NEW", now=original_time,
        )
        _require(original["manifest_sha256"] == approval["manifest_sha256"] and
                 original["witness_artifact_sha256"] ==
                 approval["witness_artifact_sha256"],
                 "ROOT_ORIGINAL_GO_SCOPE_MISMATCH")
    return approval


def reviewed_root_commands():
    """Bounded OS-only literal allowlist consumed by R309 SSH mux.

    No general shell, elevated Python, Docker, systemd, DB, network or
    repository-controlled command can cross this boundary.
    """
    commands = set()
    for path in _PATHS:
        commands.add(f"/usr/bin/sudo -n /usr/bin/test -e {path}")
        commands.add(f"/usr/bin/sudo -n /usr/bin/test -L {path}")
        commands.add(
            f"/usr/bin/sudo -n /usr/bin/stat -c '%u|%g|%a|%h|%F|%d|%i' -- {path}"
        )
    for path in _FILES:
        commands.add(f"/usr/bin/sudo -n /usr/bin/sha256sum -- {path}")
    for path in ("/var/lib", ROOT_DIRECTORY, ROOT_LOCK, ROOT_STAGE,
                 ROOT_OLD_ARCHIVE, ROOT_PIN, ROOT_TRANSITION_RECEIPT_STAGE,
                 ROOT_TRANSITION_RECEIPT):
        commands.add(f"/usr/bin/sudo -n /usr/bin/sync -- {path}")
    commands.add(f"/usr/bin/sudo -n /usr/bin/mkdir -m 0700 -- {ROOT_LOCK}")
    commands.add(f"/usr/bin/sudo -n /usr/bin/ln -T -- {ROOT_PIN} {ROOT_OLD_ARCHIVE}")
    for path in (ROOT_STAGE, ROOT_TRANSITION_RECEIPT_STAGE):
        commands.add(
            f"/usr/bin/sudo -n /usr/bin/install -m 0400 -o 0 -g 0 /dev/stdin {path}"
        )
    commands.add(f"/usr/bin/sudo -n /usr/bin/mv -T -- {ROOT_STAGE} {ROOT_PIN}")
    commands.add(
        f"/usr/bin/sudo -n /usr/bin/ln -T -- {ROOT_TRANSITION_RECEIPT_STAGE} "
        f"{ROOT_TRANSITION_RECEIPT}"
    )
    commands.add(f"/usr/bin/sudo -n /usr/bin/rm -- {ROOT_TRANSITION_RECEIPT_STAGE}")
    commands.add(INSTALL_APPROVED_COMMAND)
    for part in reviewed_stage_commands().values():
        commands.update(part.values())
    commands.update((
        STAGE_LOCK_CREATE, STAGE_INBOX_CREATE, STAGE_LOCK_STAT,
        STAGE_INBOX_STAT, STAGE_LOCK_SYNC, STAGE_INBOX_SYNC,
    ))
    return frozenset(commands)


def reviewed_stage_commands():
    """Return only pre-reviewed R280 inbox fixed role-to-command mappings.

    The controller must handle a root0700 one-shot lock, exact role bytes,
    size limits, source manifest hash and fresh authorization independently.
    GNU install creates a root0400 zero-size stage inside root0700 inbox;
    bounded append chunks never create a temporarily world-readable file.
    GNU ln -T publishes without overwrite; no cleanup/retry here.
    """
    _require(r280.SOURCE_NAMES == {
        role: name for role, name in _STAGE_SOURCE_NAMES.items()
        if role != "manifest"}, "R280_SOURCE_NAME_DRIFT")
    mapping = {}
    for role, basename in _STAGE_SOURCE_NAMES.items():
        staged = f"{STAGE_INBOX}/.r321-{role}.stage"
        final = f"{STAGE_INBOX}/{basename}"
        mapping[role] = MappingProxyType({
            "create": (
                f"/usr/bin/sudo -n /usr/bin/install -m 0400 -o 0 -g 0 "
                f"/dev/null {staged}"
            ),
            "append": (
                f"/usr/bin/sudo -n /usr/bin/dd if=/dev/stdin of={staged} "
                f"bs=8192 oflag=append conv=notrunc status=none"
            ),
            "stage_stat": (
                f"/usr/bin/sudo -n /usr/bin/stat -c {_STAGE_FMT} -- {staged}"
            ),
            "stage_hash": f"/usr/bin/sudo -n /usr/bin/sha256sum -- {staged}",
            "link": f"/usr/bin/sudo -n /usr/bin/ln -T -- {staged} {final}",
            "remove_stage": f"/usr/bin/sudo -n /usr/bin/rm -- {staged}",
            "final_stat": (
                f"/usr/bin/sudo -n /usr/bin/stat -c {_STAGE_FMT} -- {final}"
            ),
            "final_hash": f"/usr/bin/sudo -n /usr/bin/sha256sum -- {final}",
            "sync": f"/usr/bin/sudo -n /usr/bin/sync -- {staged}",
            "final_sync": f"/usr/bin/sudo -n /usr/bin/sync -- {final}",
        })
    return MappingProxyType(mapping)


def _call(host, command, *, stdin=None, statuses=(0,), cap=0):
    """Strict bridge for R309BoundedMuxHost.call with no stderr exposure."""
    _require(command in reviewed_root_commands() or
             command in (HOST_ID_COMMAND, HOST_REGION_COMMAND),
             "ROOT_COMMAND_NOT_REVIEWED")
    _require(stdin is None or type(stdin) is bytes and
             0 < len(stdin) <= 8192, "ROOT_STDIN_OVERSIZE")
    _require(type(cap) is int and cap in (0, 32, 128, 256),
             "ROOT_OUTPUT_CAP_UNSUPPORTED")
    try:
        status, output = host.call(
            command, stdin=stdin, max_output_bytes=cap, timeout_s=15)
    except Exception:
        # Neither root stdout/stderr nor protected DO selector is loggable.
        raise PinHold("ROOT_TRANSPORT_UNVERIFIED") from None
    _require(type(status) is int and status in statuses and
             type(output) is bytes and len(output) <= cap,
             "ROOT_COMMAND_STATUS_OR_OUTPUT_UNVERIFIED")
    return status, output


def _exists(host, path):
    _require(path in _PATHS, "ROOT_PATH_UNREVIEWED")
    symbol, _ = _call(
        host, f"/usr/bin/sudo -n /usr/bin/test -L {path}",
        statuses=(0, 1))
    _require(symbol == 1, "ROOT_SYMLINK_FORBIDDEN")
    exists, _ = _call(
        host, f"/usr/bin/sudo -n /usr/bin/test -e {path}",
        statuses=(0, 1))
    return exists == 0


def _stat(host, path, *, directory=False, mode=None, links=None):
    _, raw = _call(
        host, f"/usr/bin/sudo -n /usr/bin/stat -c "
        f"'%u|%g|%a|%h|%F|%d|%i' -- {path}", cap=128)
    try:
        values = raw.decode("ascii", "strict").strip("\n").split("|")
        _require(len(values) == 7, "ROOT_STAT_FORMAT")
        uid, gid, permissions, hardlinks, kind, device, inode = values
        bits, count, dev, ino = (int(permissions, 8), int(hardlinks),
                                 int(device), int(inode))
    except (ValueError, UnicodeError):
        raise PinHold("ROOT_STAT_FORMAT") from None
    _require(uid == "0" and gid == "0" and
             kind == ("directory" if directory else "regular file") and
             count >= 1 and dev > 0 and ino > 0 and
             not bits & 0o022 and
             (mode is None or bits == mode) and
             (links is None or count == links),
             "ROOT_STAT_OWNERSHIP_OR_KIND_UNVERIFIED")
    return dev, ino, count


def _sha_file(host, path, expected):
    _require(type(expected) is str and _HEX64.fullmatch(expected),
             "ROOT_EXPECTED_SHA_INVALID")
    _, raw = _call(
        host, f"/usr/bin/sudo -n /usr/bin/sha256sum -- {path}", cap=256)
    _require(raw == (expected + "  " + path + "\n").encode("ascii"),
             "ROOT_SOURCE_SHA_MISMATCH")


def _sync(host, path):
    _call(host, f"/usr/bin/sudo -n /usr/bin/sync -- {path}")


def _identity(host, context):
    _validate_context(context)
    _, droplet = _call(host, HOST_ID_COMMAND, cap=32)
    _, region = _call(host, HOST_REGION_COMMAND, cap=32)
    _require(droplet == (context.do_id + "\n").encode("ascii") and
             region == (context.do_region + "\n").encode("ascii"),
             "PROTECTED_DO_IDENTITY_MISMATCH")


def _preflight_dirs(host, *, require_lock=False):
    root_dev = None
    for directory in _DIRECTORIES:
        if directory == ROOT_LOCK and not require_lock:
            continue
        _require(_exists(host, directory), "ROOT_DIRECTORY_MISSING")
        dev, _ino, _links = _stat(
            host, directory, directory=True,
            mode=0o700 if directory in (R301_LOCK, ROOT_LOCK) else None)
        if directory == ROOT_DIRECTORY:
            root_dev = dev
        if directory == ROOT_LOCK:
            _require(dev == root_dev, "ROOT_ATOMIC_RENAME_DEVICE_MISMATCH")
    return root_dev


def _snapshot_pin(host, expected, *, links):
    _require(_exists(host, ROOT_PIN), "ROOT_PIN_MISSING")
    pin_stat = _stat(host, ROOT_PIN, mode=0o400, links=links)
    _sha_file(host, ROOT_PIN, _sha((expected + "\n").encode("ascii")))
    return pin_stat


def _bootstrap_receipt(host):
    _require(_exists(host, R301_RECEIPT), "R301_RECEIPT_MISSING")
    _stat(host, R301_RECEIPT, mode=0o400, links=1)
    _sha_file(host, R301_RECEIPT, R301_RECEIPT_SHA256)


def _new_pin_bytes(manifest_sha):
    _require(type(manifest_sha) is str and _HEX64.fullmatch(manifest_sha),
             "ROOT_PIN_NEW_DIGEST_REQUIRED")
    return (manifest_sha + "\n").encode("ascii")


def _transition_receipt(context, *, original_go_id, witness):
    """Frozen small result; resume never substitutes its new GO as origin."""
    receipt = {
        "schema": "RC6_R305_ROOT_PIN_TRANSITION_FILES_ONLY_V1",
        "status": "PIN_TRANSITION_FILES_ONLY_NOT_HOST_GO",
        "trusted_main_sha": context.main_sha,
        "candidate_sha": context.candidate_sha,
        "candidate_tree": context.candidate_tree,
        "owner_go_comment_id": original_go_id,
        "witness_artifact_sha256": context.witness_artifact_sha256,
        "witness_run_id": witness["run_id"],
        "witness_run_attempt": witness["run_attempt"],
        "previous_manifest_sha256": HISTORICAL_D_PIN,
        "root_manifest_sha256": context.manifest_sha256,
        "r301_receipt_sha256": R301_RECEIPT_SHA256,
        "paper_mode": "PRODUCTION_PAPER",
        "real_orders_allowed": False,
        "host_control_verified": False,
    }
    raw = _canonical(receipt)
    _require(0 < len(raw) <= 8192, "ROOT_RECEIPT_SIZE")
    return raw


def execute_root_pin_transition(
    host, context, *, manifest_raw, witness_raw, owner_comment,
    owner_go_id, recheck_authority, operation="NEW",
    original_go_comment=None, now=None, clock=None,
):
    """Run one owner-gated root-only pin transition on a reviewed R309 host.

    The trusted Actions controller MUST independently authenticate the
    GH owner comment, GH main-ref SHA, and witness artifact job/digest.
    It passes recheck_authority(), which must re-fetch all three as a tuple
    (current_owner_comment, current_main_sha, authenticated_witness_digest).
    For RESUME a fourth element, the authenticated original owner comment,
    is mandatory; the supplied local original is not an authority source.
    No caller can supply a shell command or target path.

    A NEW attempt is single-use and permanently reserves ROOT_LOCK before
    first write. RESUME is separately owner-approved and never deletes the
    lock/archive, reverses the pin, or retries a failed transport. Any
    ambiguous partial state remains HOLD79 for independent root forensics.
    R280 install-approved, source staging >8 KiB, unit and runtime are separate.
    """
    _validate_context(context)
    _require(callable(recheck_authority), "GH_AUTHORITY_RECHECK_REQUIRED")
    _require(now is None or (type(now) is datetime and now.tzinfo is not None),
             "ROOT_CLOCK_INVALID")
    if clock is None:
        clock = lambda: datetime.now(timezone.utc)
    _require(callable(clock), "ROOT_CLOCK_REQUIRED")

    manifest = validate_root_manifest(
        manifest_raw, expected_digest=context.manifest_sha256)
    _require(manifest["candidate_sha"] == context.candidate_sha and
             manifest["candidate_tree"] == context.candidate_tree,
             "ROOT_CANDIDATE_IDENTITY_MISMATCH")
    witness = validate_independent_witness(
        witness_raw, expected_digest=context.witness_artifact_sha256,
        manifest=manifest, context=context)
    current_time = clock() if now is None else now
    owner_go = verify_owner_go(
        owner_comment, context, owner_go_id=owner_go_id,
        operation=operation, now=current_time,
        original_go_comment=original_go_comment)
    _require(owner_go["witness_run_id"] == witness["run_id"] and
             owner_go["witness_run_attempt"] == witness["run_attempt"],
             "OWNER_GO_WITNESS_RUN_MISMATCH")
    original_id = (owner_go_id if operation == "NEW" else
                   owner_go["original_owner_go_comment_id"])
    receipt_raw = _transition_receipt(
        context, original_go_id=original_id, witness=witness)
    new_pin_hash = _sha(_new_pin_bytes(context.manifest_sha256))
    old_pin_hash = _sha(_new_pin_bytes(HISTORICAL_D_PIN))
    receipt_hash = _sha(receipt_raw)

    def check_go():
        try:
            fresh = recheck_authority()
        except Exception:
            raise PinHold("GH_AUTHORITY_TRANSPORT_UNVERIFIED") from None
        _require(type(fresh) is tuple and len(fresh) ==
                 (4 if operation == "RESUME" else 3),
                 "GH_AUTHORITY_RECHECK_RESPONSE_INVALID")
        actual, main_ref_sha, artifact_sha = fresh[:3]
        _require(type(actual) is dict and actual == owner_comment and
                 main_ref_sha == context.main_sha and
                 artifact_sha == context.witness_artifact_sha256,
                 "GH_OWNER_MAIN_OR_WITNESS_DRIFT")
        if operation == "RESUME":
            _require(type(fresh[3]) is dict and
                     fresh[3] == original_go_comment,
                     "GH_ORIGINAL_OWNER_GO_DRIFT")
        verify_owner_go(
            actual, context, owner_go_id=owner_go_id, operation=operation,
            now=clock(), original_go_comment=original_go_comment)

    # All deterministic authority checks precede any potentially slow
    # network/physical read. Identity and R301 provenance precede first write.
    check_go()
    _identity(host, context)
    _preflight_dirs(host)
    _bootstrap_receipt(host)
    _require(_exists(host, ROOT_PIN), "ROOT_PIN_MISSING")
    _stat(host, ROOT_PIN, mode=0o400, links=1 if operation == "NEW" else None)

    is_locked = _exists(host, ROOT_LOCK)
    archive_exists = _exists(host, ROOT_OLD_ARCHIVE)
    stage_exists = _exists(host, ROOT_STAGE)
    receipt_stage_exists = _exists(host, ROOT_TRANSITION_RECEIPT_STAGE)
    receipt_exists = _exists(host, ROOT_TRANSITION_RECEIPT)
    if operation == "NEW":
        _require(not any((is_locked, archive_exists, stage_exists,
                          receipt_stage_exists, receipt_exists)),
                 "ROOT_NEW_ATTEMPT_COLLISION")
        _snapshot_pin(host, HISTORICAL_D_PIN, links=1)
    else:
        _require(is_locked and archive_exists,
                 "ROOT_RECOVERY_ORIGIN_UNVERIFIED")
        _preflight_dirs(host, require_lock=True)
        _stat(host, ROOT_OLD_ARCHIVE, mode=0o400)
        _sha_file(host, ROOT_OLD_ARCHIVE, old_pin_hash)
        # In a valid partial transaction the archive is a hardlink to
        # old pin until swap, then its link count drops to one.
        old_stat = _stat(host, ROOT_OLD_ARCHIVE, mode=0o400)
        pin_stat = _stat(host, ROOT_PIN, mode=0o400)
        if old_stat[1] == pin_stat[1]:
            _require(old_stat[0] == pin_stat[0] and
                     old_stat[2] == pin_stat[2] == 2,
                     "ROOT_RECOVERY_HARDLINK_MISMATCH")
            _sha_file(host, ROOT_PIN, old_pin_hash)
            _require(not receipt_exists and not receipt_stage_exists,
                     "ROOT_RECEIPT_PREMATURE")
        else:
            _require(old_stat[2] == pin_stat[2] == 1 and
                     not stage_exists, "ROOT_RECOVERY_PIN_STATE_AMBIGUOUS")
            _sha_file(host, ROOT_PIN, new_pin_hash)
        if stage_exists:
            _stat(host, ROOT_STAGE, mode=0o400, links=1)
            _sha_file(host, ROOT_STAGE, new_pin_hash)

    check_go()  # Recheck before taking root lock/new mutation.
    if operation == "NEW":
        _call(host, f"/usr/bin/sudo -n /usr/bin/mkdir -m 0700 -- {ROOT_LOCK}")
        _sync(host, "/var/lib")
    _preflight_dirs(host, require_lock=True)
    archive_exists = _exists(host, ROOT_OLD_ARCHIVE)
    if not archive_exists:
        _snapshot_pin(host, HISTORICAL_D_PIN, links=1)
        _call(host, f"/usr/bin/sudo -n /usr/bin/ln -T -- "
                    f"{ROOT_PIN} {ROOT_OLD_ARCHIVE}")
        _sync(host, ROOT_OLD_ARCHIVE)
        _sync(host, ROOT_DIRECTORY)
    _stat(host, ROOT_OLD_ARCHIVE, mode=0o400)
    _sha_file(host, ROOT_OLD_ARCHIVE, old_pin_hash)
    archive_state = _stat(host, ROOT_OLD_ARCHIVE, mode=0o400)
    pin_state = _stat(host, ROOT_PIN, mode=0o400)
    before_swap = archive_state[1] == pin_state[1]
    if before_swap:
        _require(archive_state[0] == pin_state[0] and
                 archive_state[2] == pin_state[2] == 2,
                 "ROOT_OLD_PIN_ARCHIVE_ALIAS_MISMATCH")
        _sha_file(host, ROOT_PIN, old_pin_hash)
        if not _exists(host, ROOT_STAGE):
            _call(host, f"/usr/bin/sudo -n /usr/bin/install -m 0400 "
                        f"-o 0 -g 0 /dev/stdin {ROOT_STAGE}",
                  stdin=_new_pin_bytes(context.manifest_sha256))
            _sync(host, ROOT_STAGE)
        _stat(host, ROOT_STAGE, mode=0o400, links=1)
        _sha_file(host, ROOT_STAGE, new_pin_hash)
        # No coalesced stale "ready" bool: verify exact old inode, hash,
        # root stage and current owner GO immediately before atomic rename.
        check_go()
        _require(_stat(host, ROOT_PIN, mode=0o400, links=2) == pin_state,
                 "ROOT_PIN_CHANGED_DURING_PREFLIGHT")
        _sha_file(host, ROOT_PIN, old_pin_hash)
        _require(_stat(host, ROOT_OLD_ARCHIVE, mode=0o400, links=2) ==
                 archive_state, "ROOT_ARCHIVE_CHANGED_DURING_PREFLIGHT")
        _sha_file(host, ROOT_STAGE, new_pin_hash)
        _call(host, f"/usr/bin/sudo -n /usr/bin/mv -T -- "
                    f"{ROOT_STAGE} {ROOT_PIN}")
        _sync(host, ROOT_PIN)
        _sync(host, ROOT_DIRECTORY)

    _snapshot_pin(host, context.manifest_sha256, links=1)
    _stat(host, ROOT_OLD_ARCHIVE, mode=0o400, links=1)
    _sha_file(host, ROOT_OLD_ARCHIVE, old_pin_hash)
    _require(not _exists(host, ROOT_STAGE), "ROOT_STAGE_REMAINS_AFTER_SWAP")
    if _exists(host, ROOT_TRANSITION_RECEIPT):
        _stat(host, ROOT_TRANSITION_RECEIPT, mode=0o400)
        _sha_file(host, ROOT_TRANSITION_RECEIPT, receipt_hash)
        if _exists(host, ROOT_TRANSITION_RECEIPT_STAGE):
            first = _stat(host, ROOT_TRANSITION_RECEIPT, mode=0o400, links=2)
            second = _stat(host, ROOT_TRANSITION_RECEIPT_STAGE, mode=0o400,
                           links=2)
            _require(first[:2] == second[:2],
                     "ROOT_RECEIPT_HARDLINK_MISMATCH")
            _sha_file(host, ROOT_TRANSITION_RECEIPT_STAGE, receipt_hash)
            _call(host, f"/usr/bin/sudo -n /usr/bin/rm -- "
                        f"{ROOT_TRANSITION_RECEIPT_STAGE}")
            _sync(host, ROOT_TRANSITION_RECEIPT)
            _sync(host, ROOT_DIRECTORY)
    else:
        if not _exists(host, ROOT_TRANSITION_RECEIPT_STAGE):
            check_go()
            _call(host, f"/usr/bin/sudo -n /usr/bin/install -m 0400 "
                        f"-o 0 -g 0 /dev/stdin {ROOT_TRANSITION_RECEIPT_STAGE}",
                  stdin=receipt_raw)
            _sync(host, ROOT_TRANSITION_RECEIPT_STAGE)
        _stat(host, ROOT_TRANSITION_RECEIPT_STAGE, mode=0o400, links=1)
        _sha_file(host, ROOT_TRANSITION_RECEIPT_STAGE, receipt_hash)
        check_go()
        _call(host, f"/usr/bin/sudo -n /usr/bin/ln -T -- "
                    f"{ROOT_TRANSITION_RECEIPT_STAGE} {ROOT_TRANSITION_RECEIPT}")
        _call(host, f"/usr/bin/sudo -n /usr/bin/rm -- "
                    f"{ROOT_TRANSITION_RECEIPT_STAGE}")
        _sync(host, ROOT_TRANSITION_RECEIPT)
        _sync(host, ROOT_DIRECTORY)
    _stat(host, ROOT_TRANSITION_RECEIPT, mode=0o400, links=1)
    _sha_file(host, ROOT_TRANSITION_RECEIPT, receipt_hash)
    _snapshot_pin(host, context.manifest_sha256, links=1)
    return {
        "schema": "RC6_R305_ROOT_PIN_TRANSITION_FILES_ONLY_V1",
        "status": "PIN_TRANSITION_FILES_ONLY_NOT_HOST_GO",
        "root_manifest_sha256": context.manifest_sha256,
        "candidate_sha": context.candidate_sha,
        "owner_go_comment_id": original_id,
        "host_control_verified": False,
        "real_orders_allowed": False,
    }
