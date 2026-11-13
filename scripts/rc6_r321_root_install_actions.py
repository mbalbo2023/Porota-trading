"""R321 CODE-ONLY trusted-main ROOT staging plan; NOT a host installer.

This is an explicit fail-closed bridge toward R305/R308/R309/R280. The
immutable plan is **not** authority: the caller-supplied expected digest is
only a shape/hash check, not independent witness, owner GO or ROOT pin.
Nothing here opens SSH, runs sudo, writes files or starts Docker/systemd.

The physical entry stays HOLD79 until a separate, reviewed executor can
prove its R305 literal staging commands, R309 transport, genuine D/E artifact,
independent ROOT witness and fresh owner GO at each mutation boundary.
"""

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
from importlib import import_module
import json
import os
import re
import sys

from scripts import rc6_host_rootowned_installer_contract as r280


STAGE_ORDER = ("helper", "custody", "profiles", "approval", "unit_template", "manifest")
_SOURCE_ORDER = tuple(r280.SOURCE_ORDER)
_SCHEMA_KEYS = frozenset({"schema", "candidate_sha", "candidate_tree",
                          "paper_mode", "real_orders_allowed", "files"})
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_REASON = re.compile(r"[A-Z][A-Z0-9_]{0,70}\Z")
CHUNK_BYTES = 8192  # Matches, never expands, R309BoundedMuxHost.call stdin.
MAX_TOTAL_BYTES = 512 * 1024
MAX_TOTAL_CHUNKS = 64

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


def _canonical(doc):
    try:
        return (json.dumps(doc, ensure_ascii=True, sort_keys=True,
                           allow_nan=False, separators=(",", ":")) + "\n").encode("ascii")
    except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError) as exc:
        raise R321Hold("ROOT_JSON_CANONICALIZATION") from exc


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


def main(argv=None, *, env=None):
    """This revision never starts a physical transaction, even on trusted main."""
    options = sys.argv[1:] if argv is None else argv
    try:
        if options == ["--install-approved"]:
            raise R321Hold("PHYSICAL_TRANSPORT_NOT_CERTIFIED")
        if options == ["--check-offhost"]:
            validate_dispatch_context(os.environ if env is None else env)
            raise R321Hold("ROOT_GO_AND_ARTIFACT_NOT_INTEGRATED")
        raise R321Hold("OFFHOST_FIXED_VERB_ONLY")
    except R321Hold as exc:
        print("RC6_R321_ROOT_ACTIONS=HOLD79|reason=" + exc.reason +
              "|host_control_verified=NO_VERIFICADO|real_orders_allowed=false")
        return 79


if __name__ == "__main__":
    raise SystemExit(main())
