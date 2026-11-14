"""R321 trusted-main file observation and offhost ROOT staging plan.

This is an explicit fail-closed bridge toward R305/R308/R309/R280. The
immutable plan is **not** authority: the caller-supplied expected digest is
only a shape/hash check, not independent witness, owner GO or ROOT pin.
The library contains an explicit, narrowly bounded host-call executor using
R309's existing transport and R305's reviewed sudo command literals. That
install executor remains unconnected to the workflow/CLI. The separate file
probe can observe installed paths through R309's read-only vocabulary.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
from importlib import import_module
import json
import os
import re
import sys
from types import MappingProxyType

from scripts import rc6_host_rootowned_installer_contract as r280
from scripts import rc6_host_rootowned_installer_cli as r280_cli
from scripts import rc6_r163_artifact_byte_verifier as r163
from scripts import rc6_r302_bootstrap_receipt_preflight as r302
from scripts import rc6_r309_bounded_mux_host as r309


STAGE_ORDER = ("helper", "custody", "profiles", "approval", "unit_template", "manifest")
_SOURCE_ORDER = tuple(r280.SOURCE_ORDER)
_SCHEMA_KEYS = frozenset({"schema", "candidate_sha", "candidate_tree",
                          "paper_mode", "real_orders_allowed", "files"})
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_HEX32 = re.compile(r"[0-9a-f]{32}\Z")
_POSITIVE = re.compile(r"[1-9][0-9]{0,19}\Z")
_DO_REGION = re.compile(r"[a-z]{2,12}[0-9]{1,2}\Z")
_REASON = re.compile(r"[A-Z][A-Z0-9_]{0,70}\Z")
CHUNK_BYTES = 8192  # Matches, never expands, R309BoundedMuxHost.call stdin.
INSTALLER_RECEIPT_CAP = 512  # Full R280 JSON is 261 bytes, not R309 legacy cap 256.
MAX_TOTAL_BYTES = 512 * 1024
MAX_TOTAL_CHUNKS = 64
MIN_OWNER_GO_REMAINING = timedelta(minutes=10)
OWNER_GO_MARKER = "RC6_R321_ROOT_INSTALL_GO_V1="
_GO_KEYS = frozenset({
    "operation", "issue", "trusted_main_sha", "trusted_main_tree",
    "candidate_sha", "candidate_tree", "root_manifest_sha256",
    "witness_artifact_sha256", "witness_run_id", "witness_run_attempt",
    "release_e_run_id", "release_e_run_attempt", "release_e_artifact_id",
    "release_e_artifact_digest", "release_e_receipt_sha256",
    "release_d_run_id", "release_d_run_attempt", "release_d_artifact_id",
    "release_d_artifact_digest",
    "generation", "r305_pin_owner_go_id", "paper_mode", "real_orders_allowed",
    "host_control_verified", "host_identity_source", "nonce", "expires_at",
})

# Exact installed root-owned R280 launcher; needs independent allowance in
# R305.reviewed_root_commands, verified host authority, and isolated Python.
# It is never run by this module.
INSTALLER_COMMAND = (
    "/usr/bin/sudo -n /usr/bin/python3 -I "
    "/usr/local/sbin/porota-rc6-host-installer install-approved"
)


class R321Hold(RuntimeError):
    exit_code = 79

    def __init__(self, reason):
        self.reason = reason if type(reason) is str and _REASON.fullmatch(reason) else "UNVERIFIED"
        super().__init__("RC6_R321_ROOT_ACTIONS_HOLD79:" + self.reason)


def _require(ok, reason):
    if not ok:
        raise R321Hold(reason)


@dataclass(frozen=True)
class StageChunk:
    role: str
    index: int
    offset: int
    data: bytes


@dataclass(frozen=True)
class ProposedStagingPlan:
    candidate_sha: str
    candidate_tree: str
    manifest_sha256: str
    total_bytes: int
    chunks: tuple[StageChunk, ...]
    state: str = field(default="PROPUESTO", init=False)
    host_control_verified: bool = field(default=False, init=False)
    executable: bool = field(default=False, init=False)


@dataclass(frozen=True)
class RootInstallContext:
    """Selectors from protected Actions/GitHub, not evidence by themselves."""

    main_sha: str
    main_tree: str
    candidate_sha: str
    candidate_tree: str
    manifest_sha256: str
    witness_artifact_sha256: str
    do_id: str = field(repr=False)
    do_region: str = field(repr=False)


def _canonical(doc):
    try:
        return (json.dumps(doc, ensure_ascii=True, sort_keys=True,
                           allow_nan=False, separators=(",", ":")) + "\n").encode("ascii")
    except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError) as exc:
        raise R321Hold("ROOT_JSON_CANONICALIZATION") from exc


def _parse_owner_timestamp(value):
    _require(type(value) is str and re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value),
        "ROOT_GO_CLOCK_INVALID")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise R321Hold("ROOT_GO_CLOCK_INVALID") from exc


def _check_root_context(context):
    _require(type(context) is RootInstallContext and
             all(type(getattr(context, key)) is str and
                 _HEX40.fullmatch(getattr(context, key)) for key in
                 ("main_sha", "main_tree", "candidate_sha", "candidate_tree")) and
             all(type(getattr(context, key)) is str and
                 _HEX64.fullmatch(getattr(context, key)) for key in
                 ("manifest_sha256", "witness_artifact_sha256")) and
             type(context.do_id) is str and re.fullmatch(
                 r"[1-9][0-9]{5,19}", context.do_id) and
             type(context.do_region) is str and _DO_REGION.fullmatch(
                 context.do_region), "ROOT_CONTEXT_PROTECTED_PINS_REQUIRED")


def validate_release_e_receipt(raw, context):
    """Check exact E bytes; Actions must authenticate its own completed run."""
    _check_root_context(context)
    _require(type(raw) is bytes and 0 < len(raw) <= 32768,
             "RELEASE_E_RECEIPT_SIZE")
    try:
        receipt = r280._strict_json(raw, limit=32768)
    except r280.InstallHold as exc:
        raise R321Hold("RELEASE_E_RECEIPT_JSON_INVALID") from exc
    _require(raw == _canonical(receipt) and set(receipt) == r163.POSTCOMPLETION_RECEIPT_KEYS and
             type(receipt.get("schema_version")) is int and
             receipt["schema_version"] == 1 and
             receipt.get("status") == "RELEASE_BYTES_VERIFIED_POSTCOMPLETION" and
             receipt.get("repository") == r163.REPOSITORY and
             receipt.get("candidate_sha") == context.candidate_sha and
             receipt.get("candidate_tree_sha") == context.candidate_tree and
             receipt.get("publisher_main_sha") == context.main_sha and
             receipt.get("publisher_workflow_path") == r163.RELEASE_WORKFLOW and
             all(r163._integer(receipt[key], minimum=1) for key in (
                 "publisher_run_id", "publisher_run_attempt", "release_artifact_id",
                 "release_artifact_size_bytes", "file_count", "total_size_bytes")) and
             receipt.get("release_artifact_name") ==
             f"rc6-r251-release-{receipt['publisher_run_id']}-"
             f"{receipt['publisher_run_attempt']}-{context.candidate_sha}" and
             type(receipt.get("release_artifact_digest")) is str and
             re.fullmatch(r"sha256:[0-9a-f]{64}",
                          receipt["release_artifact_digest"]) and
             receipt["file_count"] == len(r163.RELEASE_PAYLOAD_FILES) and
             receipt.get("release_manifest_verified") is True and
             receipt.get("r144_release_go") is False and
             receipt.get("runtime_verified") is False and
             receipt.get("real_orders_physical_verified") is False,
             "RELEASE_E_RECEIPT_SCOPE_INVALID")
    return receipt


def verify_install_owner_go(
    comment, context, *, expected_comment_id, witness, release_e_receipt_raw,
    release_e_run_id, release_e_run_attempt,
    release_e_artifact_id, release_e_artifact_digest, approval, now,
    initial_budget=True,
):
    """Verify separate install authority; this never authenticates API origin.

    The trusted Actions controller must fetch and compare immutable GitHub
    owner comment, exact E run/receipt, D artifact and witness *independently*
    at each file boundary. A self-supplied comment never grants host control.
    """
    _check_root_context(context)
    _require(type(expected_comment_id) is int and expected_comment_id > 0 and
             type(now) is datetime and now.tzinfo is not None and
             type(initial_budget) is bool and
             type(release_e_run_id) is str and
             _POSITIVE.fullmatch(release_e_run_id) and
             type(release_e_run_attempt) is str and
             _POSITIVE.fullmatch(release_e_run_attempt) and
             type(release_e_artifact_id) is int and
             release_e_artifact_id > 0 and
             type(release_e_artifact_digest) is str and
             re.fullmatch(r"sha256:[0-9a-f]{64}", release_e_artifact_digest),
             "ROOT_GO_CONTEXT_INVALID")
    _require(type(witness) is dict and
             all(type(witness.get(key)) is str and
                 _POSITIVE.fullmatch(witness[key])
                 for key in ("run_id", "run_attempt")) and
             type(approval) is dict and
             type(approval.get("generation")) is str and
             _HEX32.fullmatch(approval["generation"]),
             "ROOT_GO_WITNESS_OR_GENERATION_MISSING")
    release = validate_release_e_receipt(release_e_receipt_raw, context)
    # E encodes GitHub run identifiers as integers. ROOT approval and GO encode decimal strings.
    d_run_id = str(release["publisher_run_id"])
    d_run_attempt = str(release["publisher_run_attempt"])
    _require(approval.get("candidate_sha") == context.candidate_sha and
             approval.get("candidate_tree") == context.candidate_tree and
             approval.get("artifact_digest") == release["release_artifact_digest"] and
             approval.get("run_id") == d_run_id and
             approval.get("run_attempt") == d_run_attempt and
             approval.get("paper_mode") == "PRODUCTION_PAPER" and
             approval.get("real_orders_allowed") is False,
             "ROOT_APPROVAL_RELEASE_E_DRIFT")
    _require(type(comment) is dict and comment.get("id") == expected_comment_id and
             comment.get("issue_url") ==
             "https://api.github.com/repos/mbalbo2023/Porota-trading/issues/1093" and
             type(comment.get("user")) is dict and
             comment["user"].get("login") == "mbalbo2023" and
             comment.get("author_association") == "OWNER" and
             type(comment.get("created_at")) is str and
             comment.get("created_at") == comment.get("updated_at") and
             type(comment.get("body")) is str and
             comment["body"].startswith(OWNER_GO_MARKER) and
             len(comment["body"]) <= 4096,
             "ROOT_INSTALL_OWNER_GO_AUTHORITY_UNVERIFIED")
    raw_body = comment["body"][len(OWNER_GO_MARKER):].encode("utf-8")
    try:
        go = r280._strict_json(raw_body, limit=4096)
    except r280.InstallHold as exc:
        raise R321Hold("ROOT_INSTALL_GO_JSON_INVALID") from exc
    _require(raw_body == _canonical(go) and set(go) == _GO_KEYS and
             go["operation"] == "RC6_ROOT_INSTALL_NEW_FILES_ONLY" and
             type(go["issue"]) is int and go["issue"] == 1093 and
             go["trusted_main_sha"] == context.main_sha and
             go["trusted_main_tree"] == context.main_tree and
             go["candidate_sha"] == context.candidate_sha and
             go["candidate_tree"] == context.candidate_tree and
             go["root_manifest_sha256"] == context.manifest_sha256 and
             go["witness_artifact_sha256"] == context.witness_artifact_sha256 and
             go["witness_run_id"] == witness["run_id"] and
             go["witness_run_attempt"] == witness["run_attempt"] and
             go["release_e_run_id"] == release_e_run_id and
             go["release_e_run_attempt"] == release_e_run_attempt and
             type(go["release_e_artifact_id"]) is int and
             go["release_e_artifact_id"] == release_e_artifact_id and
             go["release_e_artifact_digest"] == release_e_artifact_digest and
             go["release_e_receipt_sha256"] ==
             hashlib.sha256(release_e_receipt_raw).hexdigest() and
             go["release_d_run_id"] == d_run_id and
             go["release_d_run_attempt"] == d_run_attempt and
             type(go["release_d_artifact_id"]) is int and
             go["release_d_artifact_id"] == release["release_artifact_id"] and
             go["release_d_artifact_digest"] ==
             release["release_artifact_digest"] and
             go["generation"] == approval["generation"] and
             type(go["r305_pin_owner_go_id"]) is int and
             go["r305_pin_owner_go_id"] > 0 and
             go["paper_mode"] == "PRODUCTION_PAPER" and
             go["real_orders_allowed"] is False and
             go["host_control_verified"] is False and
             go["host_identity_source"] == "PROTECTED_DO_PINS" and
             type(go["nonce"]) is str and _HEX32.fullmatch(go["nonce"]),
             "ROOT_INSTALL_GO_SCOPE_OR_SHA_MISMATCH")
    issued, expiry = (_parse_owner_timestamp(comment["created_at"]),
                      _parse_owner_timestamp(go["expires_at"]))
    current = now.astimezone(timezone.utc)
    try:
        timely = (issued - timedelta(minutes=5) <= current and
                  current < expiry <= issued + timedelta(hours=2) and
                  expiry - current >= (MIN_OWNER_GO_REMAINING if initial_budget
                                       else timedelta()))
    except OverflowError as exc:
        raise R321Hold("ROOT_INSTALL_GO_EXPIRED_OR_TOO_SHORT") from exc
    _require(timely, "ROOT_INSTALL_GO_EXPIRED_OR_TOO_SHORT")
    return go


def validate_installer_receipt(raw, *, expected_manifest_sha256, candidate_sha):
    """Read the full trusted installed R280 receipt; never infer HOST GO."""
    _require(type(raw) is bytes and 0 < len(raw) <= INSTALLER_RECEIPT_CAP and
             type(expected_manifest_sha256) is str and
             _HEX64.fullmatch(expected_manifest_sha256) and
             type(candidate_sha) is str and _HEX40.fullmatch(candidate_sha),
             "ROOT_INSTALL_RECEIPT_SHAPE")
    try:
        doc = r280._strict_json(raw, limit=INSTALLER_RECEIPT_CAP)
        _require(raw == _canonical(doc), "ROOT_INSTALL_RECEIPT_NONCANONICAL")
        verified = r280_cli.validate_installer_receipt(
            doc, expected_manifest_sha256)
    except (r280.InstallHold, r280_cli.CliHold) as exc:
        raise R321Hold("ROOT_INSTALL_RECEIPT_R280_REJECTED") from exc
    _require(verified["candidate_sha"] == candidate_sha and
             verified["host_control_verified"] is False,
             "ROOT_INSTALL_RECEIPT_CANDIDATE_DRIFT")
    return verified


def build_staging_plan(manifest_raw, source_bytes, *, expected_manifest_sha256, now):
    """Build a bounded, byte-identical OFFHOST plan only; no authority claim.

    The digest must be delivered independently *at a later control border*;
    supplying this function with matching self-computed data never grants GO.
    R280 still verifies its own independent root-protected manifest pin.
    """
    _require(type(expected_manifest_sha256) is str and
             _HEX64.fullmatch(expected_manifest_sha256) and
             type(manifest_raw) is bytes and 0 < len(manifest_raw) <= 8192,
             "ROOT_MANIFEST_INPUT_INVALID")
    _require(hashlib.sha256(manifest_raw).hexdigest() == expected_manifest_sha256,
             "INDEPENDENT_ROOT_PIN_MISMATCH")
    try:
        manifest = r280._strict_json(manifest_raw, limit=8192)
    except r280.InstallHold as exc:
        raise R321Hold("ROOT_MANIFEST_JSON") from exc
    _require(_canonical(manifest) == manifest_raw and
             set(manifest) == _SCHEMA_KEYS and
             manifest.get("schema") == "RC6_ROOT_INSTALL_MANIFEST_V1" and
             type(manifest.get("candidate_sha")) is str and
             _HEX40.fullmatch(manifest["candidate_sha"]) and
             type(manifest.get("candidate_tree")) is str and
             _HEX40.fullmatch(manifest["candidate_tree"]) and
             manifest.get("paper_mode") == "PRODUCTION_PAPER" and
             manifest.get("real_orders_allowed") is False and
             type(manifest.get("files")) is dict and
             set(manifest["files"]) == set(_SOURCE_ORDER),
             "ROOT_MANIFEST_SCHEMA")
    _require(type(source_bytes) is dict and set(source_bytes) == set(_SOURCE_ORDER),
             "ROOT_SOURCE_ROLE_SCOPE")
    # An upstream source map is mutable. Capture each immutable byte string once,
    # before hashing, approval and chunking; subsequent reads of the caller's map
    # would let the physical stage differ from the independently reviewed manifest.
    try:
        frozen_sources = {role: source_bytes[role] for role in _SOURCE_ORDER}
    except (KeyError, RuntimeError, TypeError) as exc:
        raise R321Hold("ROOT_SOURCE_SNAPSHOT_UNVERIFIED") from exc
    _require(type(now) is datetime and now.tzinfo is not None,
             "ROOT_APPROVAL_CLOCK_REQUIRED")
    _require(all(type(raw) is bytes and 0 < len(raw) <=
                 (r280.TARGETS[role][2] if role in r280.TARGETS else 128 * 1024)
                 for role, raw in frozen_sources.items()), "ROOT_SOURCE_SIZE")
    total = len(manifest_raw) + sum(len(raw) for raw in frozen_sources.values())
    _require(total <= MAX_TOTAL_BYTES, "ROOT_STAGE_TOTAL_BUDGET")

    for role, raw in frozen_sources.items():
        expected = manifest["files"][role]
        _require(type(expected) is str and _HEX64.fullmatch(expected),
                 "ROOT_SOURCE_HASH_INVALID")
        _require(hashlib.sha256(raw).hexdigest() == expected,
                 "ROOT_SOURCE_HASH_MISMATCH")
    _require(frozen_sources["helper"].startswith(b"#!/usr/bin/python3 -I\n"),
             "ROOT_HELPER_ISOLATED_PYTHON_REQUIRED")
    try:
        approval = r280._strict_json(frozen_sources["approval"],
                                      limit=r280.TARGETS["approval"][2])
        _require(frozen_sources["approval"] == _canonical(approval),
                 "ROOT_APPROVAL_NONCANONICAL")
        r280._validate_approval(approval, manifest, frozen_sources, now=now)
    except (r280.InstallHold, OverflowError) as exc:
        raise R321Hold("ROOT_APPROVAL_R280_REJECTED") from exc

    chunks = []
    for role in STAGE_ORDER:
        raw = manifest_raw if role == "manifest" else frozen_sources[role]
        for offset in range(0, len(raw), CHUNK_BYTES):
            chunks.append(StageChunk(role, offset // CHUNK_BYTES, offset,
                                     raw[offset:offset + CHUNK_BYTES]))
    _require(len(chunks) <= MAX_TOTAL_CHUNKS and
             all(0 < len(chunk.data) <= CHUNK_BYTES for chunk in chunks),
             "ROOT_STAGE_CHUNK_BUDGET")
    return ProposedStagingPlan(
        candidate_sha=manifest["candidate_sha"],
        candidate_tree=manifest["candidate_tree"],
        manifest_sha256=expected_manifest_sha256,
        total_bytes=total,
        chunks=tuple(chunks),
    )


def validate_dispatch_context(env):
    """Trusted-main runner context gate, intentionally NOT owner GO."""
    _require(type(env) is dict or hasattr(env, "get"), "DISPATCH_CONTEXT_MISSING")
    _require(env.get("GITHUB_EVENT_NAME") == "workflow_dispatch" and
             env.get("GITHUB_REPOSITORY") == "mbalbo2023/Porota-trading" and
             env.get("GITHUB_REF") == "refs/heads/main" and
             env.get("GITHUB_ACTOR") == "mbalbo2023" and
             env.get("GITHUB_TRIGGERING_ACTOR") == "mbalbo2023" and
             type(env.get("GITHUB_SHA")) is str and
             _HEX40.fullmatch(env["GITHUB_SHA"]),
             "TRUSTED_MAIN_OWNER_DISPATCH_REQUIRED")
    return env["GITHUB_SHA"]


def require_reviewed_installer_command():
    """Require R305's trusted-code finite allowlist, never caller authority."""
    try:
        r305 = import_module("scripts.rc6_r305_root_pin_transition_guard")
        reviewed = r305.reviewed_root_commands()
    except (ImportError, AttributeError, TypeError) as exc:
        raise R321Hold("R305_INSTALLER_LITERAL_NOT_REVIEWED") from exc
    _require(type(reviewed) is frozenset and
             INSTALLER_COMMAND in reviewed,
             "R305_INSTALLER_LITERAL_NOT_REVIEWED")
    return INSTALLER_COMMAND


_STAGE_VERBS = frozenset({
    "stage_test_l", "stage_test_e", "final_test_l", "final_test_e",
    "create", "append", "stage_stat", "stage_hash", "link",
    "remove_stage", "final_stat", "final_hash", "sync", "final_sync",
})
_STAGE_CONSTANTS = (
    "STAGE_LOCK_CREATE", "STAGE_LOCK_STAT", "STAGE_LOCK_TEST_L",
    "STAGE_LOCK_TEST_E", "STAGE_LOCK_SYNC", "STAGE_INBOX_CREATE",
    "STAGE_INBOX_STAT", "STAGE_INBOX_TEST_L", "STAGE_INBOX_TEST_E",
    "STAGE_INBOX_SYNC", "INSTALL_APPROVED_COMMAND",
)


def _trusted_stage_commands(r305):
    """No caller strings, mutable allowlists, paths or shell substitutes."""
    try:
        cmds = r305.reviewed_stage_commands()
        reviewed = r305.reviewed_root_commands()
    except (AttributeError, TypeError) as exc:
        raise R321Hold("R305_TRUSTED_STAGE_API_MISSING") from exc
    _require(type(cmds) is MappingProxyType and
             set(cmds) == set(STAGE_ORDER) and
             type(reviewed) is frozenset and
             0 < len(reviewed) <= 512 and
             all(type(command) is str and len(command) <= 512 and
                 command.startswith("/usr/bin/sudo -n /usr/bin/")
                 for command in reviewed), "R305_TRUSTED_STAGE_ALLOWLIST_INVALID")
    for role in STAGE_ORDER:
        _require(type(cmds[role]) is MappingProxyType and
                 set(cmds[role]) == _STAGE_VERBS and
                 all(type(command) is str and command in reviewed
                     for command in cmds[role].values()),
                 "R305_STAGE_ROLE_COMMAND_UNREVIEWED")
    _require(all(type(getattr(r305, name, None)) is str and
                 getattr(r305, name) in reviewed
                 for name in _STAGE_CONSTANTS) and
             r305.INSTALL_APPROVED_COMMAND == INSTALLER_COMMAND,
             "R305_STAGE_ROOT_CONSTANT_UNREVIEWED")
    return cmds, reviewed


def _host_call(host, command, reviewed, *, stdin=None, cap=0,
               statuses=(0,)):
    _require(type(command) is str and command in reviewed and
             cap in (0, 32, 128, 256, INSTALLER_RECEIPT_CAP) and
             (cap != INSTALLER_RECEIPT_CAP or command == INSTALLER_COMMAND) and
             (stdin is None or type(stdin) is bytes and
              0 < len(stdin) <= CHUNK_BYTES),
             "R321_ROOT_CALL_UNREVIEWED")
    try:
        status, output = host.call(
            command, stdin=stdin, max_output_bytes=cap, timeout_s=15)
    except Exception:
        raise R321Hold("R309_ROOT_TRANSPORT_UNVERIFIED") from None
    _require(type(status) is int and status in statuses and
             type(output) is bytes and len(output) <= cap,
             "R321_ROOT_CALL_STATUS_INVALID")
    return status, output


def _absence(host, l_command, e_command, reviewed):
    """-L BEFORE -e detects dangling symlink and any stale stage path."""
    link, output = _host_call(host, l_command, reviewed, cap=0,
                              statuses=(0, 1))
    _require(link == 1 and output == b"", "ROOT_STAGE_SYMLINK_DETECTED")
    exists, output = _host_call(host, e_command, reviewed, cap=0,
                                statuses=(0, 1))
    _require(exists == 1 and output == b"", "ROOT_STAGE_PATH_COLLISION")


def _optional_root_directory(host, r305, reviewed):
    link, output = _host_call(host, r305.STAGE_INBOX_TEST_L, reviewed,
                              statuses=(0, 1))
    _require(link == 1 and output == b"", "ROOT_INBOX_SYMLINK_FORBIDDEN")
    status, output = _host_call(host, r305.STAGE_INBOX_TEST_E, reviewed,
                                statuses=(0, 1))
    _require(output == b"", "ROOT_INBOX_EXISTS_OUTPUT_INVALID")
    if status == 0:
        _stage_stat(host, r305.STAGE_INBOX_STAT, reviewed,
                    expected_kind="directory", expected_mode=0o700)
        return True
    return False


def _stage_stat(host, command, reviewed, *, expected_kind, expected_mode,
                expected_size=None, expected_links=None):
    _, raw = _host_call(host, command, reviewed, cap=128)
    try:
        _require(raw.endswith(b"\n") and raw.count(b"\n") == 1,
                 "ROOT_STAGE_STAT_FORMAT")
        values = raw.decode("ascii", "strict").rstrip("\n").split("|")
        _require(len(values) == 8, "ROOT_STAGE_STAT_FORMAT")
        owner, group, mode, links, kind, dev, inode, size = values
        mode_number, link_number, device, inode_number, bytes_number = (
            int(mode, 8), int(links), int(dev), int(inode), int(size))
    except (ValueError, UnicodeError, TypeError) as exc:
        raise R321Hold("ROOT_STAGE_STAT_FORMAT") from exc
    _require(owner == "0" and group == "0" and
             mode_number == expected_mode and kind == expected_kind and
             link_number >= 1 and device > 0 and inode_number > 0 and
             bytes_number >= 0 and
             (expected_size is None or bytes_number == expected_size) and
             (expected_links is None or link_number == expected_links),
             "ROOT_STAGE_STAT_INODE_MODE_SIZE_INVALID")
    return device, inode_number, link_number


def _stage_sha(host, command, reviewed, raw):
    prefix = "/usr/bin/sudo -n /usr/bin/sha256sum -- "
    _require(command.startswith(prefix), "ROOT_STAGE_HASH_COMMAND_INVALID")
    file_path = command[len(prefix):]
    _require(file_path.startswith(r280.INBOX_DIR + "/"),
             "ROOT_STAGE_HASH_PATH_INVALID")
    _, result = _host_call(host, command, reviewed, cap=256)
    _require(result == (hashlib.sha256(raw).hexdigest() + "  " + file_path +
                        "\n").encode("ascii"), "ROOT_STAGE_FILE_SHA_INVALID")


def _stage_one(host, cmds, reviewed, *, raw, check_go):
    """One root-only source role, from immutable preapproved plan bytes."""
    _absence(host, cmds["stage_test_l"], cmds["stage_test_e"], reviewed)
    _absence(host, cmds["final_test_l"], cmds["final_test_e"], reviewed)
    check_go()
    _host_call(host, cmds["create"], reviewed)
    first = _stage_stat(host, cmds["stage_stat"], reviewed,
                        expected_kind="regular file", expected_mode=0o400,
                        expected_size=0, expected_links=1)
    for offset in range(0, len(raw), CHUNK_BYTES):
        # Recheck both GOs and global ownership before every bounded write.
        check_go()
        _host_call(host, cmds["append"], reviewed,
                   stdin=raw[offset:offset + CHUNK_BYTES])
    _require(_stage_stat(host, cmds["stage_stat"], reviewed,
                         expected_kind="regular file", expected_mode=0o400,
                         expected_size=len(raw), expected_links=1) == first,
             "ROOT_STAGE_SOURCE_INODE_DRIFT")
    _stage_sha(host, cmds["stage_hash"], reviewed, raw)
    _host_call(host, cmds["sync"], reviewed)
    # Stage itself exists; only its link type is checked while final stays absent.
    link, output = _host_call(host, cmds["stage_test_l"], reviewed,
                              statuses=(0, 1))
    _require(link == 1 and output == b"", "ROOT_STAGE_SYMLINK_DETECTED")
    _absence(host, cmds["final_test_l"], cmds["final_test_e"], reviewed)
    _require(_stage_stat(host, cmds["stage_stat"], reviewed,
                         expected_kind="regular file", expected_mode=0o400,
                         expected_size=len(raw), expected_links=1) == first,
             "ROOT_STAGE_SOURCE_INODE_DRIFT")
    _stage_sha(host, cmds["stage_hash"], reviewed, raw)
    check_go()
    _host_call(host, cmds["link"], reviewed)
    _require(_stage_stat(host, cmds["stage_stat"], reviewed,
                         expected_kind="regular file", expected_mode=0o400,
                         expected_size=len(raw), expected_links=2)[:2] == first[:2],
             "ROOT_STAGE_LINK_INODE_MISMATCH")
    _require(_stage_stat(host, cmds["final_stat"], reviewed,
                         expected_kind="regular file", expected_mode=0o400,
                         expected_size=len(raw), expected_links=2)[:2] == first[:2],
             "ROOT_STAGE_FINAL_ALIAS_MISMATCH")
    _stage_sha(host, cmds["final_hash"], reviewed, raw)
    _host_call(host, cmds["final_sync"], reviewed)
    check_go()
    _host_call(host, cmds["remove_stage"], reviewed)
    _require(_stage_stat(host, cmds["final_stat"], reviewed,
                         expected_kind="regular file", expected_mode=0o400,
                         expected_size=len(raw), expected_links=1)[:2] == first[:2],
             "ROOT_STAGE_FINAL_INODE_CHANGED")
    _stage_sha(host, cmds["final_hash"], reviewed, raw)


_RECHECK_KEYS = frozenset({
    "owner_comment", "main_sha", "candidate_tree", "witness_artifact_sha256",
    "release_e_run_id", "release_e_run_attempt", "release_e_artifact_id",
    "release_e_artifact_digest", "release_e_receipt_sha256",
    "release_d_artifact_id", "release_d_artifact_digest",
})


def execute_root_install_files_only(
    host, context, *, manifest_raw, source_bytes, witness_raw, owner_comment,
    owner_go_id, pin_owner_comment, pin_owner_go_id, release_e_receipt_raw,
    release_e_run_id, release_e_run_attempt, release_e_artifact_id,
    release_e_artifact_digest, recheck_install_authority,
    recheck_pin_authority, clock, ownership_record_id=None, recheck_ownership=None,
):
    """Reviewed R309 transport only; one new ROOT stage+pin+R280 FILES-ONLY.

    GH recheck callbacks MUST be implemented in trusted-main Actions from
    authenticated REST (owner, main ref, witness and R251 E/D artifacts). This
    library cannot authenticate caller-provided callbacks or download source
    artifacts itself. The required recheck_ownership provider must resolve the
    complete authenticated #980 ledger to the externally expected active global
    DEPLOY_OWNER record, then project the operation, owner, two GO IDs and host.
    Its scalar projection is not a signed GO or proof of API authentication;
    the trusted controller owns that boundary. No lease expiry is invented.
    Therefore installation is NOT exposed by `main()` or workflow yet.
    Missing installed R305/R309 code, provenance or GO always HOLD79. Any
    partial ROOT filesystem state is forensics-only; never auto-resume/delete.
    """
    _check_root_context(context)
    _require(callable(recheck_install_authority) and
             callable(recheck_pin_authority) and callable(clock) and
             type(owner_go_id) is int and owner_go_id > 0 and
             type(pin_owner_go_id) is int and pin_owner_go_id > 0 and
             pin_owner_go_id != owner_go_id,
             "ROOT_SEPARATE_GO_OR_RECHECK_REQUIRED")
    _require(type(ownership_record_id) is int and ownership_record_id > 0 and
             callable(recheck_ownership), "ROOT_GLOBAL_OWNERSHIP_REQUIRED")
    try:
        r305 = import_module("scripts.rc6_r305_root_pin_transition_guard")
        r309 = import_module("scripts.rc6_r309_bounded_mux_host")
        _require(type(host) is r309.R309BoundedMuxHost,
                 "R309_SINGLE_TRUSTED_MUX_REQUIRED")
        stage_commands, reviewed = _trusted_stage_commands(r305)
        pin_context = r305.PinContext(
            main_sha=context.main_sha, main_tree=context.main_tree,
            candidate_sha=context.candidate_sha,
            candidate_tree=context.candidate_tree,
            manifest_sha256=context.manifest_sha256,
            witness_artifact_sha256=context.witness_artifact_sha256,
            do_id=context.do_id, do_region=context.do_region,
        )
    except (ImportError, AttributeError, TypeError) as exc:
        raise R321Hold("TRUSTED_R305_R309_CODE_NOT_IN_MAIN") from exc

    def comment_identity(comment):
        try:
            return r305.owner_go_identity(comment)
        except (AttributeError, TypeError, RuntimeError):
            raise R321Hold("GH_INSTALL_OWNER_MAIN_OR_ARTIFACT_DRIFT") from None

    # Freeze the original authorization before any recheck callback can
    # mutate caller-owned mappings. Presentation fields carry no authority.
    owner_identity = comment_identity(owner_comment)
    pin_identity = comment_identity(pin_owner_comment)
    expected_ownership = {
        "operation": "RC6_ROOT_INSTALL_NEW_FILES_ONLY", "authority": "DEPLOY_OWNER",
        "status": "ACTIVE", "record_id": ownership_record_id, "owner": "mbalbo2023",
        "pin_go_id": pin_owner_go_id, "install_go_id": owner_go_id,
        "do_id": context.do_id, "do_region": context.do_region,
    }

    try:
        manifest = r305.validate_root_manifest(
            manifest_raw, expected_digest=context.manifest_sha256)
        witness = r305.validate_independent_witness(
            witness_raw, expected_digest=context.witness_artifact_sha256,
            manifest=manifest, context=pin_context)
    except Exception:
        raise R321Hold("R305_ROOT_MANIFEST_OR_WITNESS_UNVERIFIED") from None
    _require(manifest["candidate_sha"] == context.candidate_sha and
             manifest["candidate_tree"] == context.candidate_tree,
             "ROOT_CANDIDATE_MANIFEST_DRIFT")
    plan = build_staging_plan(
        manifest_raw, source_bytes,
        expected_manifest_sha256=context.manifest_sha256, now=clock())
    # Reconstruct ALL source bytes from one immutable reviewed plan. Never
    # re-read the caller's source map after manifest and approval validation.
    frozen = {role: b"".join(chunk.data for chunk in plan.chunks
                              if chunk.role == role) for role in STAGE_ORDER}
    _require(set(frozen) == set(STAGE_ORDER) and
             sum(len(raw) for raw in frozen.values()) == plan.total_bytes and
             frozen["manifest"] == manifest_raw,
             "ROOT_FROZEN_STAGE_PLAN_INVALID")
    try:
        approval = r280._strict_json(
            frozen["approval"], limit=r280.TARGETS["approval"][2])
    except r280.InstallHold as exc:
        raise R321Hold("ROOT_INSTALL_APPROVAL_UNVERIFIED") from exc

    def verify_ownership(fresh):
        _require(type(fresh) is dict and set(fresh) == set(expected_ownership) and
                 all(type(fresh[key]) is type(value) and fresh[key] == value
                     for key, value in expected_ownership.items()),
                 "GH_GLOBAL_OWNERSHIP_DRIFT")

    def check_ownership():
        try:
            fresh = recheck_ownership()
        except Exception:
            raise R321Hold("GH_GLOBAL_OWNERSHIP_UNAVAILABLE") from None
        verify_ownership(fresh)
        return fresh

    def verify_pin(comment, current):
        _require(comment_identity(comment) == pin_identity,
                 "GH_PIN_OWNER_MAIN_OR_WITNESS_DRIFT")
        try:
            pin_go = r305.verify_owner_go(
                comment, pin_context, owner_go_id=pin_owner_go_id,
                operation="NEW", now=current)
        except (RuntimeError, TypeError, ValueError):
            raise R321Hold("R305_PIN_GO_UNVERIFIED") from None
        _require(pin_go["witness_run_id"] == witness["run_id"] and
                 pin_go["witness_run_attempt"] == witness["run_attempt"],
                 "R305_PIN_GO_WITNESS_RUN_MISMATCH")

    def check_pin_authority():
        try:
            fresh = recheck_pin_authority()
        except Exception:
            raise R321Hold("GH_PIN_AUTHORITY_UNAVAILABLE") from None
        current = clock()
        _require(type(fresh) is tuple and len(fresh) == 3,
                 "GH_PIN_AUTHORITY_RESPONSE_INVALID")
        comment, main_sha, witness_sha = fresh
        _require(comment_identity(comment) == pin_identity and
                 main_sha == context.main_sha and
                 witness_sha == context.witness_artifact_sha256,
                 "GH_PIN_OWNER_MAIN_OR_WITNESS_DRIFT")
        verify_pin(comment, current)
        return fresh

    def check_install_authority(*, initial=False):
        try:
            fresh = recheck_install_authority()
        except Exception:
            raise R321Hold("GH_INSTALL_AUTHORITY_UNAVAILABLE") from None
        current = clock()
        _require(type(fresh) is dict and set(fresh) == _RECHECK_KEYS and
                 comment_identity(fresh["owner_comment"]) == owner_identity and
                 fresh["main_sha"] == context.main_sha and
                 fresh["candidate_tree"] == context.candidate_tree and
                 fresh["witness_artifact_sha256"] ==
                 context.witness_artifact_sha256 and
                 fresh["release_e_run_id"] == release_e_run_id and
                 fresh["release_e_run_attempt"] == release_e_run_attempt and
                 type(fresh["release_e_artifact_id"]) is int and
                 fresh["release_e_artifact_id"] == release_e_artifact_id and
                 fresh["release_e_artifact_digest"] ==
                 release_e_artifact_digest and
                 fresh["release_e_receipt_sha256"] ==
                 hashlib.sha256(release_e_receipt_raw).hexdigest() and
                 fresh["release_d_artifact_id"] ==
                 validate_release_e_receipt(
                     release_e_receipt_raw, context)["release_artifact_id"] and
                 fresh["release_d_artifact_digest"] ==
                 approval["artifact_digest"],
                 "GH_INSTALL_OWNER_MAIN_OR_ARTIFACT_DRIFT")
        verify_install(fresh["owner_comment"], current, initial=initial)
        return fresh["owner_comment"]

    def verify_install(comment, current, *, initial=False):
        install_go = verify_install_owner_go(
            comment, context,
            expected_comment_id=owner_go_id, witness=witness,
            release_e_receipt_raw=release_e_receipt_raw,
            release_e_run_id=release_e_run_id,
            release_e_run_attempt=release_e_run_attempt,
            release_e_artifact_id=release_e_artifact_id,
            release_e_artifact_digest=release_e_artifact_digest,
            approval=approval, now=current, initial_budget=initial)
        _require(install_go["r305_pin_owner_go_id"] == pin_owner_go_id,
                 "R305_PIN_GO_ID_MISMATCH")

    def guard(*, initial=False):
        # Finish slow SSH/REST first. The lease is the last external read;
        # both GO windows are then checked at one fresh instant without I/O.
        try:
            r305._identity(host, pin_context)
        except Exception:
            raise R321Hold("ROOT_PROTECTED_DO_IDENTITY_DRIFT") from None
        install_comment = check_install_authority(initial=initial)
        pin_authority = check_pin_authority()
        ownership = check_ownership()
        current = clock()
        # Callbacks cannot redefine original or freshly returned mappings.
        _require(comment_identity(owner_comment) == owner_identity and
                 comment_identity(pin_owner_comment) == pin_identity and
                 comment_identity(install_comment) == owner_identity,
                 "GH_AUTHORITY_INPUT_DRIFT")
        verify_ownership(ownership)
        verify_install(install_comment, current, initial=initial)
        verify_pin(pin_authority[0], current)
        return pin_authority

    guard(initial=True)
    _require(type(pin_owner_comment) is dict and
             pin_owner_comment.get("id") == pin_owner_go_id and
             type(pin_owner_comment.get("body")) is str and
             pin_owner_comment["body"].startswith(r305.OWNER_GO_MARKER),
             "R305_PIN_GO_DISTINCT_REQUIRED")
    try:
        r305._preflight_dirs(host)
        r305._bootstrap_receipt(host)
    except Exception:
        raise R321Hold("R305_PIN_OR_BOOTSTRAP_PREFLIGHT_UNVERIFIED") from None

    # All paths must be free from dangling symlinks and prior attempts BEFORE
    # the first write; even a partial install has no automatic retry path.
    _absence(host, r305.STAGE_LOCK_TEST_L, r305.STAGE_LOCK_TEST_E, reviewed)
    inbox_exists = _optional_root_directory(host, r305, reviewed)
    for role in STAGE_ORDER:
        commands = stage_commands[role]
        _absence(host, commands["stage_test_l"], commands["stage_test_e"],
                 reviewed)
        _absence(host, commands["final_test_l"], commands["final_test_e"],
                 reviewed)

    guard()
    _host_call(host, r305.STAGE_LOCK_CREATE, reviewed)
    _stage_stat(host, r305.STAGE_LOCK_STAT, reviewed,
                expected_kind="directory", expected_mode=0o700)
    _host_call(host, r305.STAGE_LOCK_SYNC, reviewed)
    if not inbox_exists:
        guard()
        _host_call(host, r305.STAGE_INBOX_CREATE, reviewed)
    _stage_stat(host, r305.STAGE_INBOX_STAT, reviewed,
                expected_kind="directory", expected_mode=0o700)
    _host_call(host, r305.STAGE_INBOX_SYNC, reviewed)
    for role in STAGE_ORDER:
        _stage_one(host, stage_commands[role], reviewed,
                   raw=frozen[role], check_go=guard)
        _host_call(host, r305.STAGE_INBOX_SYNC, reviewed)

    # R305 PIN GO is separately authenticated and rechecked by its own
    # transaction. Never reuse the R321 INSTALL GO for pin publication.
    guard()
    try:
        pin_result = r305.execute_root_pin_transition(
            host, pin_context, manifest_raw=manifest_raw,
            witness_raw=witness_raw, owner_comment=pin_owner_comment,
            owner_go_id=pin_owner_go_id,
            recheck_authority=guard, operation="NEW",
            now=clock(), clock=clock)
        _require(type(pin_result) is dict and
                 pin_result.get("status") ==
                 "PIN_TRANSITION_FILES_ONLY_NOT_HOST_GO", "R305_PIN_RECEIPT_HOLD")
        r305._snapshot_pin(host, context.manifest_sha256, links=1)
    except R321Hold:
        raise
    except Exception:
        raise R321Hold("R305_ROOT_PIN_TRANSITION_HOLD") from None

    guard()
    _require(r305.INSTALL_APPROVED_COMMAND == INSTALLER_COMMAND,
             "R280_INSTALLER_LITERAL_DRIFT")
    _, receipt_bytes = _host_call(
        host, INSTALLER_COMMAND, reviewed, cap=INSTALLER_RECEIPT_CAP)
    receipt = validate_installer_receipt(
        receipt_bytes, expected_manifest_sha256=context.manifest_sha256,
        candidate_sha=context.candidate_sha)
    _require(receipt["status"] == "INSTALLED_FILES_ONLY_NOT_HOST_GO",
             "R280_INSTALL_RECEIPT_NOT_FILES_ONLY")
    return {
        "schema": "RC6_R321_ROOT_INSTALLED_FILES_ONLY_V1",
        "status": "ROOT_INSTALLED_FILES_ONLY_NOT_HOST_GO",
        "candidate_sha": context.candidate_sha,
        "manifest_sha256": context.manifest_sha256,
        "owner_go_comment_id": owner_go_id,
        "pin_go_comment_id": pin_owner_go_id,
        "paper_mode": "PRODUCTION_PAPER",
        "real_orders_allowed": False,
        "host_control_verified": False,
    }


def _probe_call(host, command, *, cap=0, statuses=(0,)):
    status, raw = host.call(command, max_output_bytes=cap)
    _require(type(status) is int and status in statuses and type(raw) is bytes and len(raw) <= cap,
             "FILE_PROBE_COMMAND_FAILED")
    return status, raw


def _probe_stat(host, path, *, directory=False, mode=None, size=None, maximum=None):
    _probe_call(host, r309.probe_test_command('! -L', path))
    _, raw = _probe_call(host, r309.probe_stat_command(path), cap=128)
    _require(raw.endswith(b'\n') and raw.count(b'\n') == 1 and
             b'\r' not in raw and b'\x00' not in raw, "FILE_PROBE_STAT_FORMAT")
    try:
        line = raw[:-1].decode('ascii')
        r302._expected_stat(path, f'STAT|{path}|{line}', directory=directory,
                            mode=mode, size=size)
        if maximum is not None:
            _require(0 < int(line.rsplit('|', 1)[1]) <= maximum,
                     "FILE_PROBE_SOURCE_SIZE")
    except (UnicodeError, ValueError, r302.R302Hold):
        raise R321Hold("FILE_PROBE_UNSAFE_STAT") from None
    return raw


def _probe_hash(host, path):
    _, raw = _probe_call(host, r309.probe_hash_command(path), cap=256)
    match = re.fullmatch(rb'([a-f0-9]{64})  ' + re.escape(path.encode('ascii')) + rb'\n', raw)
    _require(match, 'FILE_PROBE_HASH_FORMAT')
    return match.group(1).decode('ascii')


def _probe_exists(host, path):
    # A failing sudo/test -e cannot prove absence. Both negative commands
    # must succeed with empty output on this master and on the second pass.
    _probe_call(host, r309.probe_test_command('! -L', path))
    status, _ = _probe_call(host, r309.probe_test_command('-e', path), statuses=(0, 1))
    if status == 1:
        _probe_call(host, r309.probe_test_command('! -e', path))
    return status == 0


def _probe_files(host):
    # An observation is stable only when the same protected ancestors and
    # leaves bracket the hashes, including the parents of absent targets.
    private_dirs = {r302.ROOT_LOCK, r309.PROBE_GENERATIONS.rsplit('/', 1)[0]}
    parents = {path: _probe_stat(host, path, directory=True,
                                 mode=0o700 if path in private_dirs else None)
               for path in r302.ROOT_DIRECTORIES}
    expected_hashes = r302._expected_hashes()
    bootstrap_sizes = {r302.ROOT_PIN: 65, r302.ROOT_RECEIPT: len(r302._expected_receipt_bytes())}
    bootstrap_stats = {}
    for path, mode, _ in r302.ROOT_FILES:
        # R295 verifies its two bootstrap sources with the same 1 MiB bound.
        bootstrap_stats[path] = _probe_stat(host, path, mode=mode,
                                            size=bootstrap_sizes.get(path), maximum=1024**2)
        _require(_probe_hash(host, path) == expected_hashes[path], 'FILE_PROBE_BOOTSTRAP_HASH_DRIFT')

    targets = [(role, path, mode, limit) for role, (path, mode, limit) in r280.TARGETS.items()]
    targets.append(('generations', r309.PROBE_GENERATIONS, 0o700, None))
    result, first = {}, {}
    for role, path, mode, limit in targets:
        first[role] = None
        result[role] = {'state': 'ABSENT'}
        if _probe_exists(host, path):
            first[role] = _probe_stat(host, path, directory=role == 'generations',
                                      mode=mode, maximum=limit)
            result[role] = {'state': 'PRESENT'}
            if role in ('helper', 'custody'):
                result[role]['observed_sha256'] = _probe_hash(host, path)

    for role, path, mode, limit in targets:
        present = _probe_exists(host, path)
        _require(present == (first[role] is not None), 'FILE_PROBE_TARGET_CHANGED')
        if present:
            second = _probe_stat(host, path, directory=role == 'generations',
                                 mode=mode, maximum=limit)
            _require(second == first[role], 'FILE_PROBE_TARGET_CHANGED')
    for path, mode, _ in r302.ROOT_FILES:
        _require(_probe_stat(host, path, mode=mode) == bootstrap_stats[path],
                 'FILE_PROBE_BOOTSTRAP_CHANGED')
    for path in reversed(r302.ROOT_DIRECTORIES):
        _require(_probe_stat(host, path, directory=True) == parents[path],
                 'FILE_PROBE_PARENT_CHANGED')
    return result


def probe_files_only(env):
    validate_dispatch_context(env)
    _require(env.get('RC6_R321_OPERATION') == r309.PROBE_OPERATION,
             'EXPLICIT_FILE_PROBE_REQUIRED')
    run, attempt = env.get('GITHUB_RUN_ID'), env.get('GITHUB_RUN_ATTEMPT')
    _require(type(run) is str and _POSITIVE.fullmatch(run) and
             type(attempt) is str and _POSITIVE.fullmatch(attempt), 'FILE_PROBE_RUN_INVALID')
    r302.validate_environment(env)
    with r309.R309BoundedMuxHost(env, operation=r309.PROBE_OPERATION) as host:
        identity = []
        for cmd in (r309.HOST_ID_COMMAND, r309.HOST_REGION_COMMAND, r309.PROBE_UID_COMMAND):
            _, raw = _probe_call(host, cmd, cap=32)
            identity.append(raw.removesuffix(b'\n'))
        r302.verify_host_identity(b'\n'.join(identity) + b'\n', env)
        observations = _probe_files(host)
    return {'schema': 'RC6_R321_PROBE_FILES_ONLY_V1', 'trusted_main_sha': env['GITHUB_SHA'],
            'run_id': run, 'run_attempt': attempt,
            'observed_at_utc': datetime.now(timezone.utc).isoformat(),
            'bootstrap_files_verified': True, 'r271': observations,
            'host_control_verified': False, 'runtime_verified': False, 'deploy_authorized': False}


def main(argv=None, *, env=None):
    """The physical CLI can observe files. Installation remains a separate HOLD."""
    options = sys.argv[1:] if argv is None else argv
    try:
        if options == ["--probe-files-only"]:
            report = probe_files_only(os.environ if env is None else env)
            print(json.dumps(report, sort_keys=True, separators=(',', ':')))
            return 0
        if options == ["--install-approved"]:
            raise R321Hold("PHYSICAL_TRANSPORT_NOT_CERTIFIED")
        if options == ["--check-offhost"]:
            validate_dispatch_context(os.environ if env is None else env)
            raise R321Hold("ROOT_GO_AND_ARTIFACT_NOT_INTEGRATED")
        raise R321Hold("OFFHOST_FIXED_VERB_ONLY")
    except (R321Hold, r302.R302Hold, r309.R309Hold) as exc:
        print("RC6_R321_ROOT_ACTIONS=HOLD79|reason=" + exc.reason +
              "|host_control_verified=NO_VERIFICADO|real_orders_allowed=false")
        return 79
    except (OSError, ValueError):
        print('RC6_R321_ROOT_ACTIONS=HOLD79|reason=FILE_PROBE_UNVERIFIED|host_control_verified=NO_VERIFICADO')
        return 79


if __name__ == "__main__":
    raise SystemExit(main())
