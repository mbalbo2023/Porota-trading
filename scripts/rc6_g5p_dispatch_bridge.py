"""Activate one authorized material stage through the canonical Actions workflow.

Codex's GitHub connector cannot call workflow_dispatch. A draft candidate PR
reopen is only a transport for an immutable single-gate #471 receipt. The
dispatched run reauthenticates that receipt and all native prerequisites; this
bridge never issues a gate receipt or treats HTTP 204 as a material PASS.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).absolute().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import rc6_material_pr_admission as admission

STAGES = frozenset(("focal311", "focal312", "BIG-browser", "G5P-acquire",
                    "G5P-certify", "full-gov311", "full-gov312"))
BODY_KEYS = frozenset(("RC6_DISPATCH_GATE", "RC6_DISPATCH_LAUNCH_RECEIPT_URL",
                       "RC6_DISPATCH_OWNER_SESSION"))
WORKFLOW = "rc6-unified-candidate-tests.yml"


def require(value, reason):
    if not value:
        raise ValueError(reason)


def body_fields(body):
    require(type(body) is str and len(body.encode()) <= 128 * 1024,
            "BRIDGE_BOUNDED_PR_BODY_REQUIRED")
    found = {}
    for line in body.splitlines():
        row = re.fullmatch(r"(RC6_DISPATCH_[A-Z_]+)=([^\r\n]*)", line)
        if row:
            require(row[1] in BODY_KEYS and row[1] not in found,
                    "BRIDGE_UNIQUE_KNOWN_PR_CONTROL_FIELDS_REQUIRED")
            found[row[1]] = row[2]
    require(set(found) == BODY_KEYS and found["RC6_DISPATCH_GATE"] in STAGES,
            "BRIDGE_ONE_SUPPORTED_EXACT_MATERIAL_STAGE_REQUIRED")
    return found


def checkout_identity(root, sha):
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD", "HEAD^{tree}"],
                            check=True, capture_output=True, text=True, timeout=30)
    values = result.stdout.splitlines()
    require(len(values) == 2 and values[0] == sha
            and re.fullmatch(r"[0-9a-f]{40}", values[1]),
            "BRIDGE_EXACT_CANDIDATE_CHECKOUT_REQUIRED")
    require(not subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                               check=True, capture_output=True, timeout=30).stdout,
            "BRIDGE_CLEAN_CANDIDATE_CHECKOUT_REQUIRED")
    return values[1]


def prepare(*, get=None, event_reader=None, checkout=None, now=None):
    """Return exact dispatch inputs only after current owner and Source checks."""
    reader = get or admission.api
    event, custody = (event_reader or admission.actual_event)("cheap")
    require(event.get("action") == "reopened" and event["pull_request"].get("draft") is True,
            "BRIDGE_DRAFT_REOPEN_EVENT_REQUIRED")
    pr = event["pull_request"]
    fields = body_fields(pr.get("body"))
    gate = fields["RC6_DISPATCH_GATE"]
    owner = fields["RC6_DISPATCH_OWNER_SESSION"]
    launch_url = fields["RC6_DISPATCH_LAUNCH_RECEIPT_URL"]
    sha = pr["head"]["sha"]
    tree = (checkout or (lambda head: checkout_identity(ROOT, head)))(sha)
    current = now or datetime.now(timezone.utc)
    require(current.tzinfo is not None and os.environ.get("GITHUB_RUN_ATTEMPT") == "1",
            "BRIDGE_FIRST_ACTIONS_ATTEMPT_AND_UTC_REQUIRED")
    bridge_run_id=os.environ.get('GITHUB_RUN_ID','')
    require(re.fullmatch(r'[1-9][0-9]{0,19}',bridge_run_id) is not None,
            'BRIDGE_ACTUAL_ACTIONS_RUN_ID_REQUIRED')
    launch = admission.comment(launch_url, 471, get=reader)
    anchors = admission.administrative_anchors(owner, get=reader, now=current)
    auth = admission.launch_fields(launch, sha, tree, owner, gate,
                                   get=reader, anchors=anchors)
    scope = admission.candidate_scope(auth, owner=owner, anchors=anchors)
    require(scope["pr"] == pr["number"] and scope["branch"] == pr["head"]["ref"]
            and scope["base_sha"] == pr["base"]["sha"]
            and admission.stamp(launch["created_at"]) <= admission.stamp(pr["updated_at"]),
            "BRIDGE_LIVE_PR_BASE_AND_PREEXISTING_LAUNCH_REQUIRED")
    admission.fresh_source(sha, tree, gate, get=reader, candidate=scope)
    timelines = {issue: admission.recent(issue, anchors[issue], current, get=reader)
                 for issue in (471, 473)}
    for issue in (471, 473):
        admission.latest_writer(timelines[issue], issue, sha, tree, owner, current)
    admission.ops_admission(auth, owner, current, admission.recovery_anchors(
        admission.RECOVERY_OWNER, get=reader), get=reader)
    admission.dedup_admission(sha, gate)
    return {"schema": "rc6.scoped-dispatch-bridge.v1",
            "status": "READY_TO_REQUEST_DISPATCH_NOT_GATE_PASS", "gate": gate,
            "source_sha": sha, "source_tree": tree, "owner_session": owner,
            "launch_receipt_url": launch["html_url"],
            "launch_body_sha256": hashlib.sha256(launch["body"].encode()).hexdigest(),
            "pr": scope["pr"], "branch": scope["branch"],
            "event_sha256": custody["sha256"], "run_attempt": 1,
            "bridge_run_id":int(bridge_run_id),
            "G5P_GREEN": False, "G7_authorized": False,
            "DEPLOY_OWNER": "NOT_ACQUIRED", "real_orders_sent": 0}


def dispatch(row, *, opener=None):
    require(row["status"] == "READY_TO_REQUEST_DISPATCH_NOT_GATE_PASS"
            and row["gate"] in STAGES
            and type(row.get('bridge_run_id')) is int and row['bridge_run_id']>0,
            "BRIDGE_AUTHENTIC_PREPARED_STAGE_REQUIRED")
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    require(bool(token), "BRIDGE_ACTIONS_WRITE_TOKEN_REQUIRED")
    payload = {"ref": row["branch"], "inputs": {
        "source_sha": row["source_sha"], "source_tree": row["source_tree"],
        "gate": row["gate"], "launch_receipt_url": row["launch_receipt_url"],
        "owner_session": row["owner_session"],
        "bridge_run_id":str(row['bridge_run_id'])}}
    request = urllib.request.Request(
        "https://api.github.com/repos/" + admission.REPO +
        "/actions/workflows/" + WORKFLOW + "/dispatches",
        data=json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
        method="POST", headers={"Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28"})
    with (opener or urllib.request.urlopen)(request, timeout=30) as response:
        require(response.status == 204 and response.read(1) == b"",
                "BRIDGE_WORKFLOW_DISPATCH_HTTP_204_REQUIRED")
    return {**row, "status": "DISPATCH_REQUEST_ACCEPTED_NOT_GATE_PASS",
            "dispatch_http_status": 204, "G5P_GREEN": False,
            "G7_authorized": False, "DEPLOY_OWNER": "NOT_ACQUIRED"}


def publish(path, row):
    path = Path(path).absolute()
    require(not os.path.lexists(path) and path.parent.is_dir()
            and not path.parent.is_symlink() and path.parent.lstat().st_uid == os.getuid(),
            "BRIDGE_FRESH_OWN_CONTROL_PATH_REQUIRED")
    with path.open("xb") as stream:
        stream.write(admission.wire(row)); stream.flush(); os.fsync(stream.fileno())


def failure_row(error):
    """Expose a transport failure without publishing a request URL or secret."""
    literal = str(error).partition(":")[0]
    reason = literal if re.fullmatch(r"[A-Z][A-Z0-9_]{0,191}", literal) else "BRIDGE_NON_LITERAL_FAILURE"
    row = {"schema": "rc6.scoped-dispatch-bridge.v1", "status": "RED",
           "reason": reason, "class": type(error).__name__,
           "gate_pass_claimed": False, "real_orders_sent": 0}
    if isinstance(error, urllib.error.HTTPError) and type(error.code) is int \
            and 100 <= error.code <= 599:
        row["reason"] = "BRIDGE_HTTP_STATUS_" + str(error.code)
        row["http_status"] = error.code
        for header, key in (("X-RateLimit-Remaining", "rate_limit_remaining"),
                            ("X-RateLimit-Reset", "rate_limit_reset_epoch"),
                            ("Retry-After", "retry_after_seconds")):
            value = error.headers.get(header) if error.headers else None
            if value is not None and re.fullmatch(r"[0-9]{1,20}", value):
                row[key] = int(value)
    elif isinstance(error, (TimeoutError,)):
        row["reason"] = "BRIDGE_TRANSPORT_TIMEOUT"
    elif isinstance(error, urllib.error.URLError):
        row["reason"] = "BRIDGE_TRANSPORT_URL_ERROR"
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        require(os.environ.get("GITHUB_ACTIONS") == "true"
                and os.environ.get("GITHUB_REPOSITORY") == admission.REPO
                and os.environ.get("GITHUB_REPOSITORY_ID") == str(admission.REPO_ID),
                "BRIDGE_CANONICAL_ACTIONS_REPOSITORY_REQUIRED")
        result = dispatch(prepare())
        publish(args.out, result)
        print(json.dumps({"status": result["status"], "gate": result["gate"],
                          "source_sha": result["source_sha"], "G5P_GREEN": False}), flush=True)
        return 0
    except BaseException as error:
        row = failure_row(error)
        if not os.path.lexists(args.out):
            publish(args.out, row)
        print(json.dumps(row, sort_keys=True), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
