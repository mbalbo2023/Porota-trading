"""Owner acceptance of proposed contracts, distinct from native qualification.

This reader cannot write an approval or enable a gate. A scoped, in-process
acceptance is issued only after reading the immutable owner decision and live
exclusive ownership from GitHub. Native custody, quota and complete G5 evidence
remain independent obligations of their consumers.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import re
from types import MappingProxyType
import weakref

from scripts.rc6_actions_custody import digest, require, verify_owner

SCHEMA = "porota.rc6.contract-revision-acceptance.v2"
SCOPES = MappingProxyType({
    "PRIVATE_PID_BOUNDARY_V2": frozenset({"capacity-probe", "capacity-calibration"}),
    "G5A_BOUNDED_EVIDENCE_V2": frozenset({"EVIDENCE_ACQUISITION_ONLY"}),
    "PAPER_V3_ONLY_G5P_V1": frozenset({"G5P_V3_MATERIAL"}),
})
_ISSUED = weakref.WeakKeyDictionary()
G5P_LONG_RUN_LIFECYCLE = 'IMMUTABLE_DECISION_LIVE_OWNER_REVOCATION_V1'


def _g5p_activation(github, decision, values, *, source_sha, now):
    """Bind a long run to an acceptance that was live when attempt one began."""
    from scripts import rc6_material_pr_admission as admission
    require(values.get('CONTRACT_ACCEPTANCE_LIFECYCLE') == G5P_LONG_RUN_LIFECYCLE,
            'G5P_LONG_RUN_EXPLICIT_LIFECYCLE_REQUIRED')
    run_id=os.environ.get('GITHUB_RUN_ID','')
    require(re.fullmatch(r'[1-9][0-9]{0,19}',run_id) is not None
            and os.environ.get('GITHUB_ACTIONS')=='true'
            and os.environ.get('GITHUB_RUN_ATTEMPT')=='1',
            'G5P_LONG_RUN_FIRST_ACTIONS_ATTEMPT_REQUIRED')
    run=github.request('/actions/runs/'+run_id+'/attempts/1')
    event=run.get('event')
    stage=os.environ.get('RC6_GATE')
    allowed_event=(event=='workflow_dispatch'
                   or event=='pull_request' and stage=='cheap'
                   and os.environ.get('GITHUB_EVENT_NAME')=='pull_request')
    require(run.get('id')==int(run_id) and run.get('run_attempt')==1
            and run.get('head_sha')==source_sha and allowed_event
            and run.get('status')=='in_progress' and run.get('conclusion') is None
            and run.get('path')=='.github/workflows/rc6-unified-candidate-tests.yml'
            and run.get('repository',{}).get('id')==admission.REPO_ID,
            'G5P_LONG_RUN_ACTUAL_ACTIONS_OR_SOURCE_REBOUND')
    start=admission.stamp(run.get('run_started_at'))
    created=admission.stamp(decision['created_at'])
    expires=admission.stamp(values['SOURCE_LEASE_EXPIRES_UTC'])
    require(created<=start<expires and start<=now<start+timedelta(hours=6),
            'G5P_LONG_RUN_INITIAL_DECISION_NOT_LIVE_AT_ACTUAL_START')
    return start


def _g5p_decision_not_revoked(github, *, source_sha, revision, decision, owner_session, now):
    from scripts import rc6_material_pr_admission as admission
    anchors=admission.administrative_anchors(owner_session,get=github.request,now=now)
    decision_time=admission.stamp(decision['created_at'])
    for issue in (471,473):
        for row in admission.recent(issue,anchors[issue],now,get=github.request):
            values=admission.fields(row['body'])
            if (admission.stamp(row['created_at'])>=decision_time
                    and values.get('SOURCE_SHA')==source_sha
                    and values.get('CONTRACT_REVISION')==revision):
                require(values.get('RC6_CONTRACT_REVISION_AUTHORIZATION') not in
                    ('REVOKED','NOT_GRANTED','REJECTED','CANCELLED'),
                    'G5P_LONG_RUN_CONTRACT_DECISION_REVOKED')


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
    decision=None;values=None;long_run=False
    if revision=='PAPER_V3_ONLY_G5P_V1':
        match = re.fullmatch(r"https://github\.com/mbalbo2023/Porota-trading/issues/471#issuecomment-([1-9][0-9]*)",
                             approval_url)
        require(match is not None, "CONTRACT_OWNER_DECISION_URL_REQUIRED")
        decision=github.request('/issues/comments/'+match[1])
        values=_fields(decision['body'])
        long_run=values.get('CONTRACT_ACCEPTANCE_LIFECYCLE')==G5P_LONG_RUN_LIFECYCLE
    activation=(_g5p_activation(github,decision,values,source_sha=source_sha,now=current)
        if long_run else None)
    owner_options={'decision_activation_utc':activation} if long_run else {}
    body_digest = verify_owner(github, approval_url, sha=source_sha, tree=source_tree,
        owner=owner_session, now=current, plan_sha256=contract_sha256,
        authorization_field="RC6_CONTRACT_REVISION_AUTHORIZATION",**owner_options)
    match = re.fullmatch(r"https://github\.com/mbalbo2023/Porota-trading/issues/471#issuecomment-([1-9][0-9]*)",
                         approval_url)
    require(match is not None, "CONTRACT_OWNER_DECISION_URL_REQUIRED")
    if decision is None:
        decision=github.request('/issues/comments/'+match[1]);values=_fields(decision['body'])
    else:
        require(decision.get('html_url')==approval_url and decision.get('id')==int(match[1]),
                'CONTRACT_INITIAL_DECISION_URL_REBOUND')
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
    if long_run:
        _g5p_decision_not_revoked(github,source_sha=source_sha,revision=revision,
            decision=decision,owner_session=owner_session,now=current)
    expiration = datetime.fromisoformat(values["SOURCE_LEASE_EXPIRES_UTC"].replace("Z", "+00:00"))
    if long_run:expiration=min(current+timedelta(seconds=30),activation+timedelta(hours=6))
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
