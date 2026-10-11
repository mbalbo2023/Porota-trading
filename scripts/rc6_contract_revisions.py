"""Owner acceptance of proposed contracts, distinct from native qualification.

This reader cannot write an approval or enable a gate. A scoped, in-process
acceptance is issued only after reading the immutable owner decision and live
exclusive ownership from GitHub. Native custody, quota and complete G5 evidence
remain independent obligations of their consumers.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from types import MappingProxyType
import weakref

from scripts.rc6_actions_custody import digest, require, verify_owner

SCHEMA = "porota.rc6.contract-revision-acceptance.v2"
SCOPES = MappingProxyType({
    "PRIVATE_PID_BOUNDARY_V2": frozenset({"capacity-probe", "capacity-calibration"}),
    "G5A_BOUNDED_EVIDENCE_V2": frozenset({"EVIDENCE_ACQUISITION_ONLY"}),
})
_ISSUED = weakref.WeakKeyDictionary()


class AuthenticatedRevision:
    """Non-serializable acceptance; neither this class nor JSON is a pass flag."""
    __slots__ = ("revision", "scope", "source_sha", "source_tree", "contract_sha256",
                 "owner_session", "approval_body_sha256", "expires_utc", "approval_url",
                 "paired_comment_id", "paired_body_sha256", "__weakref__")

    def __new__(cls, *args, **kwargs):
        raise ValueError("CONTRACT_OWNER_ACCEPTANCE_FACTORY_REQUIRED")

    def __setattr__(self, name, value):
        raise AttributeError("CONTRACT_OWNER_ACCEPTANCE_IMMUTABLE")


def _fields(body):
    require(type(body) is str and len(body.encode()) <= 256 * 1024,
            "CONTRACT_OWNER_DECISION_BOUND")
    values = {}
    for line in body.splitlines():
        row = re.fullmatch(r"([A-Z][A-Z0-9_]*|real_orders_sent)=([^\r\n]*)", line)
        if row:
            require(row[1] not in values, "CONTRACT_DUPLICATE_OWNER_DECISION_FIELD")
            values[row[1]] = row[2]
    return values


def _binding(revision, scope, source_sha, source_tree, contract_sha256):
    require(revision in SCOPES and scope in SCOPES[revision], "CONTRACT_UNKNOWN_REVISION_OR_SCOPE")
    require(all(type(value) is str and re.fullmatch(r"[0-9a-f]{40}", value)
                for value in (source_sha, source_tree))
            and type(contract_sha256) is str and re.fullmatch(r"[0-9a-f]{64}", contract_sha256),
            "CONTRACT_EXACT_SOURCE_AND_DOCUMENT_REQUIRED")
    return {"revision": revision, "scope": scope, "source_sha": source_sha,
            "source_tree": source_tree, "contract_sha256": contract_sha256}


def authenticate_revision(github, approval_url, *, owner_session, revision, scope,
                          source_sha, source_tree, contract_sha256, now=None):
    """Authenticate explicit owner acceptance; NEVER synthesise that decision.

    The comment must be a new unedited paired decision, with the full contract
    digest and single scope. The existing ownership reader checks history and
    supersession. Acceptance is not a statement that native equivalence, G0 or
    G5 have passed, and cannot itself authorize a workload.
    """
    binding = _binding(revision, scope, source_sha, source_tree, contract_sha256)
    current = now or datetime.now(timezone.utc)
    require(current.tzinfo is not None, "CONTRACT_UTC_CLOCK_REQUIRED")
    body_digest = verify_owner(github, approval_url, sha=source_sha, tree=source_tree,
        owner=owner_session, now=current, plan_sha256=contract_sha256,
        authorization_field="RC6_CONTRACT_REVISION_AUTHORIZATION")
    match = re.fullmatch(r"https://github\.com/mbalbo2023/Porota-trading/issues/471#issuecomment-([1-9][0-9]*)",
                         approval_url)
    require(match is not None, "CONTRACT_OWNER_DECISION_URL_REQUIRED")
    decision = github.request("/issues/comments/" + match[1])
    values = _fields(decision["body"])
    require(digest(decision["body"].encode()) == body_digest,
            "CONTRACT_OWNER_DECISION_CHANGED_DURING_READ")
    twin = github.request("/issues/comments/" + values["OWNER_RECEIPT_473"])
    twin_values = _fields(twin["body"])
    required = {"CONTRACT_ACCEPTANCE_SCHEMA": SCHEMA,
        "RC6_CONTRACT_REVISION_AUTHORIZATION": "APPROVED",
        "CONTRACT_DECISION_ORIGIN": "EXPLICIT_OWNER_DECISION",
        "CONTRACT_REVISION": revision, "CONTRACT_SCOPE": scope,
        "CONTRACT_DOCUMENT_SHA256": contract_sha256,
        "CONTRACT_GATES_ALREADY_GREEN": "false",
        "NATIVE_EQUIVALENCE_FROM_APPROVAL_CLAIMED": "false",
        "FINAL_CANDIDATE_ELIGIBLE": "false", "DEPLOY_AUTHORIZATION": "NOT_GRANTED"}
    require(all(values.get(key) == twin_values.get(key) == value
                for key, value in required.items()), "CONTRACT_EXPLICIT_PAIRED_OWNER_ACCEPTANCE_REQUIRED")
    # Recheck the twin payload as well: concurrent edits cannot bind stale bytes.
    require(twin.get('id') == int(values['OWNER_RECEIPT_473'])
            and twin.get("created_at") == twin.get("updated_at")
            and digest(twin["body"].encode()) == values["OWNER_RECEIPT_473_SHA256"],
            "CONTRACT_OWNER_PAIR_CHANGED_DURING_READ")
    expiration = datetime.fromisoformat(values["SOURCE_LEASE_EXPIRES_UTC"].replace("Z", "+00:00"))
    values_for_context = {**binding, "owner_session": owner_session,
        "approval_body_sha256": body_digest, "expires_utc": expiration,
        "approval_url": approval_url, "paired_comment_id": twin['id'],
        "paired_body_sha256": values['OWNER_RECEIPT_473_SHA256']}
    context = object.__new__(AuthenticatedRevision)
    for name, value in values_for_context.items():
        object.__setattr__(context, name, value)
    _ISSUED[context] = MappingProxyType(values_for_context)
    return context


def validate_revision_approval(context, *, revision, scope, source_sha, source_tree,
                               contract_sha256, now=None):
    """Reject copied/forged/deserialized/stale approval and every rebound field."""
    binding = _binding(revision, scope, source_sha, source_tree, contract_sha256)
    require(type(context) is AuthenticatedRevision and context in _ISSUED,
            "CONTRACT_AUTHENTICATED_OWNER_ACCEPTANCE_REQUIRED")
    original = _ISSUED[context]
    require(all(getattr(context, name) == value for name, value in original.items())
            and all(original[name] == value for name, value in binding.items()),
            "CONTRACT_ACCEPTANCE_BINDING_REBOUND")
    current = now or datetime.now(timezone.utc)
    require(current.tzinfo is not None and current < original["expires_utc"],
            "CONTRACT_ACCEPTANCE_EXPIRED")
    return context
