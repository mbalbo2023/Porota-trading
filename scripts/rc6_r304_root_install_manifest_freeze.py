"""R304 offline proposal of a *root-install* manifest, never OWNER/HOST GO.

This is deliberately not a workflow, an installer, an approval authority, or
the R251 D release manifest. Four explicitly SHA-pinned source byte strings
and a fully specified short-lived PAPER approval intent are the inputs.
Exactly five roles are frozen, including the generated approval. The output
cannot authorize itself: an independent owner must review and approve its
digest outside this module, followed by separate host transaction/rechecks.

No filesystem, SSH, sudo, Docker, systemd, DB or network operation is made.
Callers must never print raw sources or an approval with sensitive metadata.
"""

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import re
from types import MappingProxyType

from scripts import rc6_host_rootowned_installer_contract as r280


SOURCE_ROLES = frozenset({"helper", "custody", "profiles", "unit_template"})
ALL_ROLES = SOURCE_ROLES | {"approval"}
_MANIFEST_KEYS = frozenset({
    "schema", "candidate_sha", "candidate_tree", "paper_mode",
    "real_orders_allowed", "files",
})
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_REASON = re.compile(r"[A-Z][A-Z0-9_]{0,70}\Z")


class FreezeHold(RuntimeError):
    """A malformed source or intent; never an installer/runtime finding."""

    exit_code = 79

    def __init__(self, reason):
        self.reason = reason if type(reason) is str and _REASON.fullmatch(reason) else "UNVERIFIED"
        super().__init__("RC6_R304_MANIFEST_HOLD79:" + self.reason)


def _require(condition, reason):
    if not condition:
        raise FreezeHold(reason)


def _canonical(doc):
    try:
        raw = (json.dumps(doc, sort_keys=True, separators=(",", ":"),
                          allow_nan=False, ensure_ascii=True) + "\n").encode("ascii")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise FreezeHold("JSON_CANONICALIZATION_INVALID") from exc
    return raw


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class ProposedRootManifest:
    """Immutable offhost snapshot; its digest is not an approval signature."""

    manifest_bytes: bytes
    approval_bytes: bytes
    manifest_sha256: str
    files: MappingProxyType
    state: str = field(default="PROPUESTO", init=False)
    owner_go_verified: bool = field(default=False, init=False)
    host_control_verified: bool = field(default=False, init=False)
    deploy_authorized: bool = field(default=False, init=False)


def validate_root_manifest_bytes(raw):
    """Validate root-manifest *shape only*, not source provenance or OWNER GO.

    In particular, the R251 101203-byte bundle manifest is NOT a valid root
    install manifest. A digest of arbitrary JSON is never pin authority.
    """
    _require(type(raw) is bytes and 0 < len(raw) <= 8192,
             "ROOT_MANIFEST_SIZE")
    try:
        doc = r280._strict_json(raw, limit=8192)
    except r280.InstallHold as exc:
        raise FreezeHold("ROOT_MANIFEST_JSON_INVALID") from exc
    _require(set(doc) == _MANIFEST_KEYS and
             doc.get("schema") == "RC6_ROOT_INSTALL_MANIFEST_V1" and
             type(doc.get("candidate_sha")) is str and
             _HEX40.fullmatch(doc["candidate_sha"]) and
             type(doc.get("candidate_tree")) is str and
             _HEX40.fullmatch(doc["candidate_tree"]) and
             doc.get("paper_mode") == "PRODUCTION_PAPER" and
             doc.get("real_orders_allowed") is False and
             type(doc.get("files")) is dict and
             set(doc["files"]) == ALL_ROLES and
             all(type(value) is str and _HEX64.fullmatch(value)
                 for value in doc["files"].values()),
             "ROOT_INSTALL_MANIFEST_SCHEMA")
    return doc


def freeze_root_install_manifest(*, candidate_sha, candidate_tree,
                                 source_bytes, expected_source_sha256,
                                 approval_intent, now):
    """Freeze exact root bundle bytes for independent review, never for GO.

    `expected_source_sha256` and full `approval_intent` must come from outside
    this function. Comparing supplied hashes with source bytes is NOT proof
    that those hashes came from an independent, trusted owner. The root R280
    verifier and OWNER GO must both be repeated at the later physical border.
    """
    _require(type(candidate_sha) is str and _HEX40.fullmatch(candidate_sha) and
             type(candidate_tree) is str and _HEX40.fullmatch(candidate_tree),
             "CANDIDATE_SHA_OR_TREE_INVALID")
    _require(type(source_bytes) is dict and set(source_bytes) == SOURCE_ROLES and
             type(expected_source_sha256) is dict and
             set(expected_source_sha256) == SOURCE_ROLES,
             "ROOT_SOURCE_ROLE_SCOPE")
    _require(type(now) is datetime and now.tzinfo is not None,
             "APPROVAL_CLOCK_INVALID")
    _require(type(approval_intent) is dict and
             set(approval_intent) == r280._APPROVAL_KEYS,
             "APPROVAL_INTENT_REQUIRED")

    frozen = {}
    for role in sorted(SOURCE_ROLES):
        raw, pinned = source_bytes[role], expected_source_sha256[role]
        maximum = r280.TARGETS[role][2] if role in r280.TARGETS else 128 * 1024
        _require(type(raw) is bytes and 0 < len(raw) <= maximum and
                 type(pinned) is str and _HEX64.fullmatch(pinned),
                 "ROOT_SOURCE_SIZE_OR_HASH_INVALID")
        _require(_sha256(raw) == pinned, "ROOT_SOURCE_INDEPENDENT_HASH_MISMATCH")
        frozen[role] = raw  # bytes are immutable; no caller mapping retained
    _require(frozen["helper"].startswith(b"#!/usr/bin/python3 -I\n"),
             "ROOT_HELPER_ISOLATED_PYTHON_REQUIRED")

    approval_bytes = _canonical(approval_intent)
    _require(0 < len(approval_bytes) <= r280.TARGETS["approval"][2],
             "ROOT_APPROVAL_SIZE")
    frozen["approval"] = approval_bytes
    manifest = {
        "schema": "RC6_ROOT_INSTALL_MANIFEST_V1",
        "candidate_sha": candidate_sha,
        "candidate_tree": candidate_tree,
        "paper_mode": "PRODUCTION_PAPER",
        "real_orders_allowed": False,
        "files": {role: _sha256(frozen[role]) for role in sorted(ALL_ROLES)},
    }
    manifest_bytes = _canonical(manifest)
    validate_root_manifest_bytes(manifest_bytes)
    try:
        # Reparse the *sealed approval bytes*, not the mutable caller dict.
        # Otherwise a concurrent mutation could validate an intent different
        # from the one whose bytes/hash will be installed (TOCTOU).
        frozen_approval = r280._strict_json(
            approval_bytes, limit=r280.TARGETS["approval"][2])
        # R280's pure checker validates every explicit approval field,
        # source digest, candidate identity, PAPER gate and <=4h TTL.
        r280._validate_approval(frozen_approval, manifest, frozen, now=now)
    except (r280.InstallHold, OverflowError) as exc:
        raise FreezeHold("ROOT_APPROVAL_R280_CONTRACT_MISMATCH") from exc

    return ProposedRootManifest(
        manifest_bytes=manifest_bytes,
        approval_bytes=approval_bytes,
        manifest_sha256=_sha256(manifest_bytes),
        files=MappingProxyType(dict(frozen)),
    )
