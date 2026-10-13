"""Validate a fresh GitHub WIP observation before emitting an owner heartbeat.

No session-store value, incomplete Git identity, or unverified remote observation
may become a posted authority field. This helper grants no gate/deploy authority.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re

REPOSITORY = "mbalbo2023/Porota-trading"
WORKSTREAM = "WS-RC6-CONVERGENCE-468-469-470-20261005"
OWNER = "CODEX_RC6_ARCHITECTURAL_RCA_20261008_1205UTC"
WIP_BRANCH = "wip/rc6-architectural-rca-20261008-1606UTC"
PR_HEADS = {476: "dfc240a478cf08e0c6a9b0ba1b760307b09a9565",
            477: "756d37b93aa26bb6395dac481bf3c2dda9d034d7"}
ACTIVE_STATUSES = ("in_progress", "queued", "waiting", "requested", "pending")


def require(value, reason):
    if not value:
        raise ValueError(reason)


def stamp(value):
    require(type(value) is str and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value),
            "OWNER_RECEIPT_EXPLICIT_UTC_REQUIRED")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def identity(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{40}", value),
            "OWNER_RECEIPT_COMPLETE_GIT_IDENTITY_REQUIRED")
    return value


def build_receipt(observation, *, now, details, title="RC6 — lease WIP verificado",
                  lease_seconds=1140, renewal_seconds=660):
    """All checks finish before the caller can post a single byte.

    observation is a fresh, direct GitHub API observation. Its two latest_writer
    results must have been produced by rc6_material_pr_admission, not inferred
    from an earlier heartbeat. A changed WIP SHA may explicitly retain the
    previous live lease identity until this new pair has been remotely verified.
    """
    require(type(observation) is dict, "OWNER_RECEIPT_OBSERVATION_REQUIRED")
    require(observation.get("repository") == REPOSITORY
            and observation.get("workstream") == WORKSTREAM
            and observation.get("owner") == OWNER
            and observation.get("branch") == WIP_BRANCH,
            "OWNER_RECEIPT_EXACT_WIP_SCOPE_REQUIRED")
    current = stamp(now)
    observed = stamp(observation.get("observed_at_utc"))
    require(timedelta(0) <= current-observed <= timedelta(seconds=60),
            "OWNER_RECEIPT_FRESH_GITHUB_OBSERVATION_REQUIRED")
    sha, tree = identity(observation.get("source_sha")), identity(observation.get("source_tree"))
    require(observation.get("github_branch_sha") == sha
            and observation.get("github_commit_sha") == sha
            and observation.get("github_commit_tree") == tree,
            "OWNER_RECEIPT_REMOTE_SHA_TREE_BINDING_REQUIRED")
    prs = observation.get("prs")
    require(type(prs) is dict and set(prs) == {"476", "477"},
            "OWNER_RECEIPT_BOTH_PRS_REQUIRED")
    for number, expected in PR_HEADS.items():
        pr = prs[str(number)]
        require(type(pr) is dict and pr.get("head_sha") == expected
                and pr.get("state") == "open" and pr.get("draft") is True
                and pr.get("merged") is False,
                "OWNER_RECEIPT_REMOTE_PR_CHANGED")
    runs = observation.get("active_runs")
    require(type(runs) is dict and set(runs) == set(ACTIVE_STATUSES)
            and all(type(runs[s]) is int and runs[s] == 0 for s in ACTIVE_STATUSES),
            "OWNER_RECEIPT_ACTIVE_OR_UNKNOWN_ACTIONS")
    leases = observation.get("latest_writer")
    require(type(leases) is dict and set(leases) == {"471", "473"},
            "OWNER_RECEIPT_VERIFIED_PAIR_REQUIRED")
    previous_sha = identity(observation.get("previous_lease_sha", sha))
    previous_tree = identity(observation.get("previous_lease_tree", tree))
    for issue in ("471", "473"):
        lease = leases[issue]
        require(type(lease) is dict, "OWNER_RECEIPT_VERIFIED_PAIR_REQUIRED")
        fields = lease.get("fields", {})
        require(type(fields) is dict and fields.get("WORKSTREAM_ID") == WORKSTREAM
                and fields.get("WRITE_OWNER") == fields.get("INTEGRATION_OWNER") == OWNER
                and fields.get("SESSION_SUCCESSOR", fields.get("SESSION")) == OWNER
                and fields.get("DEPLOY_OWNER") == "NOT_ACQUIRED"
                and fields.get("RELEASED") == "false"
                and fields.get("SOURCE_SHA") == previous_sha
                and fields.get("SOURCE_TREE") == previous_tree,
                "OWNER_RECEIPT_LIVE_PAIR_SCOPE_OR_IDENTITY_CHANGED")
        require(type(lease.get("comment_id")) is int and lease["comment_id"] > 0
                and lease.get("url") == f"https://github.com/{REPOSITORY}/issues/{issue}#issuecomment-{lease['comment_id']}"
                and lease.get("author") == "mbalbo2023"
                and type(lease.get("body_sha256")) is str
                and re.fullmatch(r"[0-9a-f]{64}", lease["body_sha256"])
                and lease.get("all_post_anchor_records_and_old_comment_edits_checked") is True
                and lease.get("maximum_lease_seconds") == 1200
                and current < stamp(lease.get("lease_expires_utc")),
                "OWNER_RECEIPT_CURRENT_AUTHENTIC_PAIR_REQUIRED")
    require(type(lease_seconds) is int and type(renewal_seconds) is int
            and 0 < renewal_seconds < lease_seconds <= 1200,
            "OWNER_RECEIPT_LEASE_OR_RENEWAL_UNBOUNDED")
    require(type(title) is str and 0 < len(title) <= 160 and "\n" not in title
            and type(details) is str and 0 < len(details) <= 8192
            and not re.search(r"(?m)^\s*[A-Za-z][A-Za-z0-9_]*\s*=", details)
            and not re.search(r"\b(undefined|None)\b", title + "\n" + details),
            "OWNER_RECEIPT_NARRATIVE_CANNOT_OVERRIDE_AUTHORITY")
    iso = lambda value: value.strftime("%Y-%m-%dT%H:%M:%SZ")
    fields = {
        "WORKSTREAM_ID": WORKSTREAM, "SESSION": OWNER, "SESSION_SUCCESSOR": OWNER,
        "WRITE_OWNER": OWNER, "INTEGRATION_OWNER": OWNER, "DEPLOY_OWNER": "NOT_ACQUIRED",
        "BRANCH": WIP_BRANCH, "BASE_SHA": PR_HEADS[476], "SOURCE_SHA": sha, "SOURCE_TREE": tree,
        "SOURCE_LEASE_EXPIRES_UTC": iso(current + timedelta(seconds=lease_seconds)),
        "NEXT_RENEWAL_UTC": iso(current + timedelta(seconds=renewal_seconds)), "RELEASED": "false",
        "MODE": "PRODUCTION_PAPER / SIMULATION", "real_orders_sent": "0",
        "REAL_ROUTES": "BLOCKED / NOT_CALLED", "PPI_WATCH": "UNCHANGED",
        "HEAVY_GATES_AUTHORIZED": "false", "G0_G8_QUALIFICATION": "false",
        "FINAL_CANDIDATE_ELIGIBLE": "false",
    }
    return title + "\n\n" + "\n".join(f"{k}={v}" for k, v in fields.items()) + "\n\n" + details


def publish_pair(observation, post, *, now, details, **options):
    """An invalid/missing field prevents both issue writes, including the first."""
    body = build_receipt(observation, now=now, details=details, **options)
    require(callable(post), "OWNER_RECEIPT_POST_CALLBACK_REQUIRED")
    result = []
    for issue in (471, 473):
        result.append(post(issue, body))
    # Publication alone is not authority: callers must read and authenticate the
    # actual remote pair before resuming mutation or authorizing any gate.
    return {"published": result, "remote_pair_verified": False, "qualification": False}
