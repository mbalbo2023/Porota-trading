"""Authenticate a finished G5 operation; never revive a launch approval.

The only factory input is an in-process authenticated Actions archive. Past
decision freshness is checked at the real run/operation UTC. Current exclusive
ownership and the full revocation timelines are checked independently now.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import os
import re
from types import MappingProxyType
import weakref

from scripts import rc6_material_pr_admission as admission
from scripts import rc6_contract_revisions as revisions

SCOPE = "FINISHED_G5_SOURCE_CERTIFICATION_ONLY"
_ISSUED = weakref.WeakKeyDictionary()


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


class HistoricalG5Certification:
    __slots__ = ("source_sha", "source_tree", "contract_sha256", "revision", "scope", "owner_session",
        "approval_url", "approval_body_sha256", "paired_comment_id", "paired_body_sha256", "accepted_utc",
        "run_id", "run_attempt", "native_started_utc", "native_fin_utc", "reviewed_utc", "capture_sha256", "__weakref__")

    def __new__(cls, *args, **kwargs):
        raise ValueError("G5B_HISTORICAL_CERTIFICATION_FACTORY_REQUIRED")

    def __setattr__(self, name, value):
        raise AttributeError("G5B_HISTORICAL_CERTIFICATION_IMMUTABLE")


def native_stamp(value):
    require(type(value) is str and re.fullmatch(
        r'20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|\+00:00)', value),
        'G5B_ACTUAL_NATIVE_UTC_STAMP_REQUIRED')
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def _decision(pointer, *, source_sha, source_tree, contract_sha256, owner, get, actual_run_started, native_started,
              native_closed=None):
    from scripts import rc6_g5_contract as g5
    require(type(pointer) is dict and set(pointer) == {"approval_url", "approval_body_sha256", "paired_comment_id",
        "paired_body_sha256", "accepted_utc"}, "G5B_ORIGINAL_IMMUTABLE_DECISION_POINTER_REQUIRED")
    match = re.fullmatch(r"https://github\.com/mbalbo2023/Porota-trading/issues/471#issuecomment-([1-9][0-9]*)",
        pointer.get("approval_url", ""))
    require(match is not None and type(pointer.get("paired_comment_id")) is int and pointer["paired_comment_id"] > 0,
        "G5B_ORIGINAL_PAIRED_DECISION_URL_REQUIRED")
    rows = [get("/issues/comments/" + identifier) for identifier in (match[1], str(pointer["paired_comment_id"]))]
    accepted = native_stamp(pointer["accepted_utc"])
    common = {"CONTRACT_ACCEPTANCE_SCHEMA": revisions.SCHEMA, "RC6_CONTRACT_REVISION_AUTHORIZATION": "APPROVED",
        "CONTRACT_DECISION_ORIGIN": "EXPLICIT_OWNER_DECISION", "CONTRACT_REVISION": g5.REVISION,
        "CONTRACT_SCOPE": g5.SCOPE, "CONTRACT_DOCUMENT_SHA256": contract_sha256,
        "CONTRACT_GATES_ALREADY_GREEN": "false", "NATIVE_EQUIVALENCE_FROM_APPROVAL_CLAIMED": "false",
        "FINAL_CANDIDATE_ELIGIBLE": "false", "DEPLOY_AUTHORIZATION": "NOT_GRANTED",
        "SOURCE_SHA": source_sha, "SOURCE_TREE": source_tree, "WRITE_OWNER": owner, "INTEGRATION_OWNER": owner,
        "SESSION_SUCCESSOR": owner, "DEPLOY_OWNER": "NOT_ACQUIRED", "RELEASED": "false",
        "MODE": "PRODUCTION_PAPER / SIMULATION", "real_orders_sent": "0"}
    values = []
    for issue, identifier, digest, row in zip((471, 473), (int(match[1]), pointer["paired_comment_id"]),
            (pointer["approval_body_sha256"], pointer["paired_body_sha256"]), rows):
        require(type(row) is dict and row.get("id") == identifier
            and row.get("html_url") == f"https://github.com/{admission.REPO}/issues/{issue}#issuecomment-{identifier}"
            and row.get("issue_url") == f"https://api.github.com/repos/{admission.REPO}/issues/{issue}"
            and row.get("user", {}).get("login") == "mbalbo2023"
            and row.get("created_at") == row.get("updated_at") and type(row.get("body")) is str
            and re.fullmatch(r"[0-9a-f]{64}", str(digest))
            and hashlib.sha256(row["body"].encode()).hexdigest() == digest,
            "G5B_HISTORICAL_OWNER_DECISION_EDITED_DELETED_OR_REBOUND")
        fields = revisions._fields(row["body"])
        require(all(fields.get(key) == value for key, value in common.items()),
            "G5B_HISTORICAL_EXPLICIT_SOURCE_CONTRACT_ACCEPTANCE_REQUIRED")
        created, expires = admission.stamp(row["created_at"]), admission.stamp(fields.get("SOURCE_LEASE_EXPIRES_UTC"))
        require(timedelta(0) < expires - created <= timedelta(minutes=20)
            and created <= actual_run_started < expires and created <= accepted < expires
            and actual_run_started <= accepted <= (native_closed or native_started)
            and (native_closed is None or native_started <= accepted),
            "G5B_HISTORICAL_REAL_RUN_OR_NATIVE_LAUNCH_OUTSIDE_INITIAL_ACCEPTANCE")
        values.append(fields)
    require(values[0].get("OWNER_RECEIPT_473") == str(pointer["paired_comment_id"])
        and values[0].get("OWNER_RECEIPT_473_SHA256") == pointer["paired_body_sha256"]
        and values[0].get("WORKSTREAM_ID") == values[1].get("WORKSTREAM_ID"),
        "G5B_HISTORICAL_IMMUTABLE_DECISION_PAIR_REBOUND")
    return rows, values[0]


def authenticate_historical_certification(archive_context, *, get, now=None):
    """No AuthenticatedRevision is constructed, extended or deserialized here."""
    from scripts import rc6_g5b_evidence as evidence
    from scripts import rc6_architectural_gates as gates
    from scripts import rc6_owned_gate_lease as leases
    from scripts import rc6_g5_contract as g5
    state = evidence.registered_archive(archive_context)
    row, capture = state["row"], archive_context.capture
    current = now or datetime.now(timezone.utc)
    require(current.tzinfo is not None, "G5B_CURRENT_UTC_REQUIRED")
    run = get("/actions/runs/" + str(row["run_id"]))
    attempt = get("/actions/runs/" + str(row["run_id"]) + "/attempts/1")
    gates.artifact_origin(state["reference"], get("/actions/artifacts/" + str(state["reference"]["artifact_id"])),
        run, attempt, source_sha=row["source_sha"])
    index = evidence.capture_document(capture("ownership_index"))
    require(index.get("source_sha") == row["source_sha"] and index.get("source_tree") == row["source_tree"]
        and index.get("run_id") == row["run_id"] and index.get("run_attempt") == 1,
        "G5B_HISTORICAL_OWNERSHIP_SOURCE_RUN_REBOUND")
    gates.verify_owned_gate_lease_index(index, row, archive_context.archive)
    commands = [entry for entry in index["commands"] if entry.get("label") == "g5a-original1202"]
    require(len(commands) == 1, "G5B_HISTORICAL_EXACT_ORIGINAL_COMMAND_REQUIRED")
    lease = gates.document(gates.capture_member(archive_context.archive, commands[0]["lease"]["path"]))
    context = lease["context"]
    owner = context["owner_session"]
    document, quota = evidence.capture_document(capture("g5b_complete")), evidence.capture_document(capture("g5a_quota"))
    pointer = document.get("owner_revision_decision")
    require(type(pointer) is dict and pointer.get("accepted_utc") == quota.get("owner_decision_lifecycle", {}).get("accepted_utc")
        and quota.get("owner_decision_lifecycle", {}).get("mode") == g5.CONTRACT["owner_decision_lifecycle"]
        and quota.get("owner_decision_lifecycle", {}).get("live_continuation_required") is True,
        "G5B_ACTUAL_SAME_NATIVE_OPERATION_IMMUTABLE_DECISION_LIFECYCLE_REQUIRED")
    started, closed = admission.stamp(lease["native_started_utc"]), admission.stamp(lease["native_fin_utc"])
    require(admission.stamp(attempt["run_started_at"]) <= started <= closed <= admission.stamp(attempt["updated_at"])
        and closed <= current, "G5B_ACTUAL_FIN_MUST_PRECEDE_CERTIFICATION")
    original, fields = _decision(pointer, source_sha=row["source_sha"], source_tree=row["source_tree"],
        contract_sha256=g5.CONTRACT_SHA256, owner=owner, get=get,
        actual_run_started=admission.stamp(attempt["run_started_at"]), native_started=started, native_closed=closed)
    anchors = admission.administrative_anchors(owner, get=get, now=current)
    timelines = {issue: admission.recent(issue, anchors[issue], current, get=get) for issue in (471, 473)}
    for issue in (471, 473):
        admission.latest_writer(timelines[issue], issue, row["source_sha"], row["source_tree"], owner, current)
        require(any(entry["id"] == original[0 if issue == 471 else 1]["id"] for entry in timelines[issue]),
            "G5B_ORIGINAL_DECISION_MISSING_FROM_FULL_CURRENT_TIMELINE")
        for entry in timelines[issue]:
            values = revisions._fields(entry.get("body", ""))
            if values.get("SOURCE_SHA") == row["source_sha"] and values.get("CONTRACT_REVISION") == g5.REVISION:
                require(values.get("CONTRACT_REVISION_REVOKED") not in ("true", "REVOKED")
                    and values.get("CONTRACT_DECISION_REVOKED") not in ("true", "REVOKED")
                    and values.get("RC6_CONTRACT_REVISION_AUTHORIZATION") not in ("REVOKED", "DENIED"),
                    "G5B_CURRENT_OWNER_CONTRACT_REVOCATION")
    launch = admission.comment(context["launch_receipt_url"], 471, get=get)
    scope = admission.candidate_scope(admission.fields(launch["body"]), owner=owner, anchors=anchors)
    admission.fresh_source(row["source_sha"], row["source_tree"], get=get, candidate=scope)
    for original_row in original:
        require(admission.same_immutable_comment(original_row, get("/issues/comments/" + str(original_row["id"]))),
            "G5B_HISTORICAL_DECISION_CHANGED_DURING_CERTIFICATION")
    result = object.__new__(HistoricalG5Certification)
    values = {"source_sha": row["source_sha"], "source_tree": row["source_tree"], "contract_sha256": g5.CONTRACT_SHA256,
        "revision": g5.REVISION, "scope": SCOPE, "owner_session": owner, **pointer,
        "run_id": row["run_id"], "run_attempt": 1, "native_started_utc": lease["native_started_utc"],
        "native_fin_utc": lease["native_fin_utc"], "reviewed_utc": current.isoformat(),
        "capture_sha256": state["archive_sha256"]}
    for key, value in values.items(): object.__setattr__(result, key, value)
    _ISSUED[result] = (os.getpid(), MappingProxyType(values), archive_context)
    return result


def validate_historical_certification(context, *, row, archive_context=None):
    require(type(context) is HistoricalG5Certification and context in _ISSUED,
        "G5B_AUTHENTIC_FINISHED_OPERATION_CERTIFICATION_REQUIRED")
    pid, original, capture = _ISSUED[context]
    require(pid == os.getpid() and all(getattr(context, key) == value for key, value in original.items())
        and context.scope == SCOPE and context.source_sha == row["source_sha"] and context.source_tree == row["source_tree"]
        and context.run_id == row["run_id"] and context.run_attempt == row["run_attempt"] == 1
        and (archive_context is None or archive_context is capture), "G5B_HISTORICAL_CERTIFICATION_SCOPE_REBOUND")
    return context
