#!/usr/bin/env python3
"""Read-only host origin probe and runner-only GitHub proof for A151 reuse.

The GitHub token stays on the Actions runner.  The host receives only the
bounded result after the runner checks the exact failed Deploy and approvals.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from datetime import datetime, timezone
import urllib.request

ORIGIN_SCHEMA = "RC6_PAPER_CAUCION_FEE_RESTATEMENT_DEPLOY_ORIGIN_V1"
PROBE_SCHEMA = "RC6_PAPER_CAUCION_FEE_RESTATEMENT_ORIGIN_PROBE_V1"
CONTINUITY_SCHEMA = "RC6_PAPER_CAUCION_FEE_RESTATEMENT_CONTINUITY_V1"
WORKFLOW = ".github/workflows/porota-deploy-v2-promote.yml"
SHA = re.compile(r"[0-9a-f]{40}\Z")
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


def _seal(payload):
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _receipt(path, candidate):
    if (path.is_symlink() or not path.is_file() or path.stat().st_size > 65536 or
            path.stat().st_nlink != 1):
        raise ValueError("PAPER_RESTATEMENT_PRIOR_FAILED_HOST_RECEIPT_RED")
    raw = path.read_bytes()
    receipt = json.loads(raw)
    if (type(receipt) is not dict or receipt.get("candidate_sha") != candidate or
            receipt.get("status") != "FIX_FORWARD_VALIDATION_REQUIRED" or
            receipt.get("installation_started") is not True or
            type(receipt.get("real_orders_sent")) is not int or
            receipt["real_orders_sent"] != 0 or receipt.get("ppi_watch_accessed") is not False):
        raise ValueError("PAPER_RESTATEMENT_PRIOR_FAILED_HOST_RECEIPT_RED")
    recorded = datetime.fromisoformat(receipt["recorded_at"])
    if recorded.tzinfo is None or recorded.utcoffset() is None or recorded > datetime.now(timezone.utc):
        raise ValueError("PAPER_RESTATEMENT_PRIOR_FAILED_HOST_CLOCK_RED")
    return hashlib.sha256(raw).hexdigest(), recorded.isoformat()


def probe(db_path, candidate_sha, receipt_dir):
    """No host mutation; verify seals before returning bounded origin metadata."""
    db_path = Path(db_path)
    if (SHA.fullmatch(candidate_sha) is None or db_path.is_symlink() or
            not db_path.is_file() or db_path.stat().st_nlink != 1):
        raise ValueError("PAPER_RESTATEMENT_ORIGIN_PROBE_INPUT_RED")
    with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA busy_timeout=2000")
        deadline = time.monotonic() + 3
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        db.execute("BEGIN")
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                           "AND name IN ('paper_caucion_fee_restatements', "
                                           "'paper_caucion_fee_restatement_origin')")}
        if not tables:
            return {"schema": PROBE_SCHEMA, "status": "FRESH"}
        if tables != {"paper_caucion_fee_restatements", "paper_caucion_fee_restatement_origin"}:
            raise ValueError("PAPER_RESTATEMENT_ORIGIN_PROBE_PARTIAL_RED")
        receipts = [dict(r) for r in db.execute(
            "SELECT * FROM paper_caucion_fee_restatements ORDER BY paper_id LIMIT 7")]
        origins = [dict(r) for r in db.execute(
            "SELECT * FROM paper_caucion_fee_restatement_origin LIMIT 2")]
        if len(receipts) != 6 or len(origins) != 1:
            raise ValueError("PAPER_RESTATEMENT_ORIGIN_PROBE_COUNT_RED")
        origin = origins[0]
        for row in receipts:
            if len(json.dumps(row, allow_nan=False)) > 8192:
                raise ValueError("PAPER_RESTATEMENT_ORIGIN_PROBE_ROW_BOUND")
            seal = row.pop("seal_sha256")
            if seal != _seal(row):
                raise ValueError("PAPER_RESTATEMENT_ORIGIN_PROBE_SEAL_RED")
            row["seal_sha256"] = seal
        combined = _seal([r["seal_sha256"] for r in receipts])
        seal = origin.pop("seal_sha256")
        if (origin.get("id") != 1 or origin.get("schema") != ORIGIN_SCHEMA or
                origin.get("combined_receipt_sha256") != combined or seal != _seal(origin) or
                any(r["candidate_sha"] != origin.get("candidate_sha") for r in receipts)):
            raise ValueError("PAPER_RESTATEMENT_ORIGIN_PROBE_SEAL_RED")
        origin["seal_sha256"] = seal
        if len(json.dumps(origin, allow_nan=False)) > 4096:
            raise ValueError("PAPER_RESTATEMENT_ORIGIN_PROBE_ROW_BOUND")
    result = {"schema": PROBE_SCHEMA, "status": "SEALED_ORIGIN", "origin": origin}
    if origin["candidate_sha"] != candidate_sha:
        digest, timestamp = _receipt(Path(receipt_dir) /
                                     ("quiescence-" + origin["candidate_sha"] + ".json"),
                                     origin["candidate_sha"])
        known = {r["known_at_utc"] for r in receipts}
        if (len(known) != 1 or datetime.fromisoformat(timestamp) <
                datetime.fromisoformat(next(iter(known)))):
            raise ValueError("PAPER_RESTATEMENT_PRIOR_FAILED_HOST_CLOCK_RED")
        result.update(host_receipt_sha256=digest, host_receipt_recorded_at=timestamp)
    return result


def authorize(probe_payload, candidate_sha, repo, get):
    """Run on Actions only. Reject a prior successful/in-progress Deploy."""
    if (SHA.fullmatch(candidate_sha) is None or REPO.fullmatch(repo) is None or
            type(probe_payload) is not dict or probe_payload.get("schema") != PROBE_SCHEMA):
        raise ValueError("PAPER_RESTATEMENT_CONTINUITY_INPUT_RED")
    if probe_payload.get("status") == "FRESH" and set(probe_payload) == {"schema", "status"}:
        return None
    if probe_payload.get("status") != "SEALED_ORIGIN":
        raise ValueError("PAPER_RESTATEMENT_CONTINUITY_PROBE_RED")
    origin = probe_payload["origin"]
    if (type(origin) is not dict or origin.get("schema") != ORIGIN_SCHEMA or
            SHA.fullmatch(str(origin.get("candidate_sha", ""))) is None):
        raise ValueError("PAPER_RESTATEMENT_CONTINUITY_ORIGIN_RED")
    if origin["candidate_sha"] == candidate_sha:
        return None  # Same-candidate idempotent replay still checks the DB.
    if set(probe_payload) != {"schema", "status", "origin", "host_receipt_sha256",
                              "host_receipt_recorded_at"}:
        raise ValueError("PAPER_RESTATEMENT_CONTINUITY_RECEIPT_PROOF_RED")
    run_id = origin["run_id"]
    if type(run_id) is not str or re.fullmatch(r"[1-9][0-9]*", run_id) is None:
        raise ValueError("PAPER_RESTATEMENT_CONTINUITY_RUN_ID_RED")
    run = get(f"repos/{repo}/actions/runs/{run_id}")
    if (run.get("id") != int(run_id) or run.get("name") != "RC6 Deploy V2 promote frozen candidate" or
            run.get("path") != WORKFLOW or run.get("event") != "push" or
            run.get("head_branch") != "deploy/rc6-pr69-isolated-20260915" or
            run.get("status") != "completed" or run.get("conclusion") != "failure" or
            run.get("head_sha") != origin["deploy_sha"] or
            run.get("run_attempt") != int(origin["run_attempt"])):
        raise ValueError("PAPER_RESTATEMENT_PRIOR_GITHUB_DEPLOY_NOT_FAILED")
    commit = get(f"repos/{repo}/commits/{origin['deploy_sha']}")
    parent = get(f"repos/{repo}/commits/{origin['candidate_sha']}")
    if (commit.get("sha") != origin["deploy_sha"] or
            [p.get("sha") for p in commit.get("parents", [])][1:] != [origin["candidate_sha"]] or
            (commit.get("commit") or {}).get("tree", {}).get("sha") != origin["candidate_tree"] or
            (parent.get("commit") or {}).get("tree", {}).get("sha") != origin["candidate_tree"]):
        raise ValueError("PAPER_RESTATEMENT_PRIOR_DEPLOY_TREE_RED")
    run_started = datetime.fromisoformat(run["run_started_at"].replace("Z", "+00:00"))
    run_updated = datetime.fromisoformat(run["updated_at"].replace("Z", "+00:00"))
    receipt_at = datetime.fromisoformat(probe_payload["host_receipt_recorded_at"])
    if (not run_started <= receipt_at <= run_updated or
            run_updated > datetime.now(timezone.utc)):
        raise ValueError("PAPER_RESTATEMENT_PRIOR_RUN_RECEIPT_CLOCK_RED")
    for field, decision in (("owner_approval_comment_id", "RC6_624_APPROVE_DEPLOY_AND_COLD2"),
                            ("owner_deferred_comment_id", "RC6_624_APPROVE_DEPLOY_WITH_HOST_STRESS_DEFERRED")):
        row = get(f"repos/{repo}/issues/comments/{origin[field]}")
        literal = (f"{decision} candidate={origin['candidate_sha']} "
                   f"tree={origin['candidate_tree']}")
        at = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        if (row.get("id") != origin[field] or
                row.get("issue_url") != f"https://api.github.com/repos/{repo}/issues/624" or
                (row.get("user") or {}).get("login") != "mbalbo2023" or
                row.get("body", "").strip() != literal or
                row.get("created_at") != row.get("updated_at") or at > run_started):
            raise ValueError("PAPER_RESTATEMENT_PRIOR_OWNER_AUTHORIZATION_RED")
    return {"schema": CONTINUITY_SCHEMA, "new_candidate_sha": candidate_sha,
            "prior_origin_seal_sha256": origin["seal_sha256"],
            "prior_receipt_sha256": probe_payload["host_receipt_sha256"],
            "prior_run_conclusion": "failure", "prior_workflow_path": WORKFLOW}


def _github_get(repo, token):
    def get(path):
        if not path.startswith("repos/" + repo + "/"):
            raise ValueError("PAPER_RESTATEMENT_GITHUB_PATH_RED")
        request = urllib.request.Request("https://api.github.com/" + path, headers={
            "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"})
        with urllib.request.urlopen(request, timeout=25) as response:
            raw = response.read(524289)
        if len(raw) > 524288:
            raise ValueError("PAPER_RESTATEMENT_GITHUB_RESPONSE_BOUND")
        return json.loads(raw)
    return get


def main(argv=None):
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--host-probe", action="store_true")
    group.add_argument("--authorize-runner", action="store_true")
    parser.add_argument("--db")
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--receipt-dir")
    parser.add_argument("--probe-json")
    parser.add_argument("--repo")
    args = parser.parse_args(argv)
    if args.host_probe:
        if not args.db or not args.receipt_dir or args.probe_json or args.repo:
            parser.error("host probe parameters invalid")
        print(json.dumps(probe(args.db, args.candidate_sha, args.receipt_dir), sort_keys=True))
    else:
        if not args.probe_json or not args.repo or args.db or args.receipt_dir:
            parser.error("runner parameters invalid")
        source = Path(args.probe_json)
        if source.is_symlink() or not source.is_file() or source.stat().st_size > 8192:
            raise ValueError("PAPER_RESTATEMENT_PROBE_FILE_RED")
        proof = authorize(json.loads(source.read_bytes()), args.candidate_sha,
                          args.repo, _github_get(args.repo, os.environ["GH_TOKEN"]))
        print(json.dumps(proof, sort_keys=True))


if __name__ == "__main__":
    main()
