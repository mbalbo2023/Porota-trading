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
from zoneinfo import ZoneInfo
import urllib.request

ORIGIN_SCHEMA = "RC6_PAPER_CAUCION_FEE_RESTATEMENT_DEPLOY_ORIGIN_V2"
PROBE_SCHEMA = "RC6_PAPER_CAUCION_FEE_RESTATEMENT_ORIGIN_PROBE_V1"
CONTINUITY_SCHEMA = "RC6_PAPER_CAUCION_FEE_RESTATEMENT_CONTINUITY_V1"
INTRADAY_MODES = frozenset({"OPEN", "A151_HELD_OPEN", "A151_HELD_STOPPED",
                            "CLOSED_A171_PENDING_FIX_FORWARD",
                            "CLOSED_A174_PENDING_FIX_FORWARD"})
QUIESCENCE_MODES = INTRADAY_MODES | frozenset({
    "CLOSED", "DEGRADED_CLOSED_20261026", "CLOSED_STOPPED_FAILED_RESCUE",
    "CLOSED_A143_PENDING_FIX_FORWARD",
})
A143_SOURCE_SHA = "2325eba329878c62a61cb47bb66d33ccf279bd4a"
A143_TREE_SHA = "e66e3756769e297687a062bec2fa6e8599c0a275"
A143_IMAGE_ID = "sha256:90e54acb1189a03acbed4b9228943ab556bdbe3a01fd515285951fc90eda93e7"
A169_STOPPED_CANDIDATE = "b778c8063e96574ddf859907abb99fae6f4e3484"
A169_STOPPED_RECEIPT_SHA256 = "ac2d6ec4bb3fe2a2ce809d89034cb564c1a3ce790de21838b654abc787471f81"
A171_FAILED_CANDIDATE = "4ce63a68c668cadd2a0aa7e8aded44ae4a3c46a6"
A171_FAILED_RECEIPT_SHA256 = "7a5e73359e85ef99796607228876443680f56bfe26ce201f4664474f700eabb5"
A171_LOADED_IMAGE_ID = "sha256:e61052af13c01d9ce76e92f38b50c794dac8a146427e132d43a8a80554a879aa"
A174_FAILED_CANDIDATE = "b6e6ce75f29fe117aaa42b0bfcd9386ea93a0aa0"
A174_FAILED_RECEIPT_SHA256 = "364c9bb9f013da603345c90219e7c889d5b04ecff951afe08f4bfc7ec03c1528"
A174_LOADED_IMAGE_ID = "sha256:9ca6e7ca98f11bbb82353434f54ffab15b218ec54319c83ae1661aaf897a40e9"
A174_MARKER_SHA256 = "0191482515683e5184871d63e6030208e0b22ddfc727845b06c4fc9b59d5c6f9"
A174_FROZEN_SHA256 = "4715ef897a9216242d0604e06e7df8ceb30f47f48552ac0d282013e04de9a687"
OWNED = {"porota_production_observer", "porota_production_dashboard"}
WORKFLOW = ".github/workflows/porota-deploy-v2-promote.yml"
SHA = re.compile(r"[0-9a-f]{40}\Z")
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


def _seal(payload):
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def validate_prior_receipt(receipt, candidate, quiescence_mode):
    """Bind the failed host receipt to its exact original safety admission.

    A151_HELD_OPEN is an A143 NO_GO hold, never a market-open suspension.
    Keep the recorded operator hold, flat A143 identity and clean stop chain.
    The normal Deploy admission still checks live host state separately.
    """
    if (type(receipt) is not dict or quiescence_mode not in QUIESCENCE_MODES or
            receipt.get("candidate_sha") != candidate or
            receipt.get("status") != "FIX_FORWARD_VALIDATION_REQUIRED" or
            receipt.get("installation_started") is not True or
            type(receipt.get("real_orders_sent")) is not int or
            receipt["real_orders_sent"] != 0 or
            receipt.get("ppi_watch_accessed") is not False):
        raise ValueError("PAPER_RESTATEMENT_PRIOR_FAILED_HOST_RECEIPT_RED")
    if quiescence_mode == "OPEN":
        if receipt.get("market_open_suspended") is not True:
            raise ValueError("PAPER_RESTATEMENT_PRIOR_FAILED_HOST_RECEIPT_RED")
    elif receipt.get("market_open_suspended") is not False:
        raise ValueError("PAPER_RESTATEMENT_PRIOR_FAILED_HOST_RECEIPT_RED")
    prior = receipt.get("a143_previous_proof")
    if quiescence_mode == "CLOSED_A171_PENDING_FIX_FORWARD":
        a171 = receipt.get("a171_previous_proof")
        if (receipt.get("a171_pending_adopted") is not True or
                receipt.get("a171_origin_candidate") != A171_FAILED_CANDIDATE or
                receipt.get("a171_origin_sha256") != A171_FAILED_RECEIPT_SHA256 or
                type(a171) is not dict or
                a171.get("origin_sha256") != A171_FAILED_RECEIPT_SHA256 or
                a171.get("image_id") != A171_LOADED_IMAGE_ID or
                a171.get("flat_book", {}).get("real_orders_sent") != 0 or
                type(a171.get("operator_receipt_sha256")) is not str or
                re.fullmatch(r"[0-9a-f]{64}",a171["operator_receipt_sha256"]) is None):
            raise ValueError("PAPER_RESTATEMENT_A171_ORIGIN_RED")
    if quiescence_mode == "CLOSED_A174_PENDING_FIX_FORWARD":
        a174 = receipt.get("a174_previous_proof")
        if (receipt.get("a174_pending_adopted") is not True or
                receipt.get("a174_origin_candidate") != A174_FAILED_CANDIDATE or
                receipt.get("a174_origin_sha256") != A174_FAILED_RECEIPT_SHA256 or
                receipt.get("inflight") != "ABSENT_OWNED_RUNTIME_STOPPED" or
                type(a174) is not dict or
                a174.get("origin_candidate") != A174_FAILED_CANDIDATE or
                a174.get("origin_sha256") != A174_FAILED_RECEIPT_SHA256 or
                a174.get("image_id") != A174_LOADED_IMAGE_ID or
                a174.get("marker_sha256") != A174_MARKER_SHA256 or
                a174.get("frozen_sha256") != A174_FROZEN_SHA256 or
                a174.get("flat_book", {}).get("real_orders_sent") != 0 or
                a174.get("flat_book", {}).get("process_state") != "WAITING_MARKET" or
                a174.get("flat_book", {}).get("session_state") not in
                    {"CLOSED", "MARKET_CLOSED"} or
                a174.get("failure_flat_book", {}).get("process_state") != "DEGRADED" or
                a174.get("failure_flat_book", {}).get("session_state") != "CLOSED" or
                a174.get("failure_flat_book", {}).get("real_orders_sent") != 0 or
                type(a174.get("containers")) is not dict or
                set(a174["containers"]) != OWNED or
                any(type(row) is not dict or
                    row.get("image") != A174_LOADED_IMAGE_ID or
                    re.fullmatch(r"[0-9a-f]{64}",str(row.get("id", ""))) is None
                    for row in a174["containers"].values()) or
                type(a174.get("operator_receipt_sha256")) is not str or
                re.fullmatch(r"[0-9a-f]{64}",a174["operator_receipt_sha256"]) is None):
            raise ValueError("PAPER_RESTATEMENT_A174_ORIGIN_RED")
    if quiescence_mode == "A151_HELD_STOPPED" and (
            receipt.get("a169_origin_candidate") != A169_STOPPED_CANDIDATE or
            receipt.get("a169_origin_sha256") != A169_STOPPED_RECEIPT_SHA256):
        raise ValueError("PAPER_RESTATEMENT_A169_STOPPED_ORIGIN_RED")
    if quiescence_mode in {"A151_HELD_OPEN", "A151_HELD_STOPPED"}:
        if (receipt.get("a143_pending_adopted") is not True or
                any(receipt.get(k) is not False for k in (
                    "open_paper_carryover", "paused_postclose", "intraday_caucion",
                    "pending_caucion")) or
                receipt.get("spot_carryover") is not None or
                receipt.get("caucion_carryover") is not None or
                type(prior) is not dict or prior.get("close") is not None or
                prior.get("source_sha") != A143_SOURCE_SHA or
                prior.get("candidate_tree_sha") != A143_TREE_SHA or
                prior.get("image_id") != A143_IMAGE_ID or
                any(type(prior.get(k)) is not str or
                    re.fullmatch(r"[0-9a-f]{64}", prior[k]) is None
                    for k in ("marker_sha256", "frozen_sha256", "prior_receipt_sha256"))):
            raise ValueError("PAPER_RESTATEMENT_A151_HELD_PROOF_RED")
        book, hold = prior.get("flat_book"), prior.get("operator_hold")
        if (type(book) is not dict or
                (book.get("process_state"), book.get("session_state"),
                 book.get("source_sha"), book.get("real_orders_sent")) != (
                    "DEGRADED", "BOOT_CALENDAR", A143_SOURCE_SHA, 0) or
                type(book.get("real_orders_sent")) is not int or
                type(prior.get("critical_admission")) is not dict or
                type(hold) is not dict or hold.get("admission") != "A151_HELD_OPEN" or
                hold.get("business_day_art") != "2026-10-28" or
                hold.get("paper_execution_authorized") is not False or
                type(hold.get("operator_receipt_sha256")) is not str or
                re.fullmatch(r"[0-9a-f]{64}", hold["operator_receipt_sha256"]) is None or
                type(hold.get("operator_operation_id")) is not str or
                re.fullmatch(r"A151-[1-9][0-9]{0,15}-[1-9][0-9]{0,3}",
                             hold["operator_operation_id"]) is None):
            raise ValueError("PAPER_RESTATEMENT_A151_HELD_PROOF_RED")
        try:
            observed = datetime.fromisoformat(hold["observed_at_utc"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("PAPER_RESTATEMENT_A151_HELD_CLOCK_RED") from exc
        if (observed.tzinfo is None or observed.utcoffset() is None or
                observed.astimezone(ZoneInfo("America/Argentina/Buenos_Aires"))
                .date().isoformat() != "2026-10-28"):
            raise ValueError("PAPER_RESTATEMENT_A151_HELD_CLOCK_RED")
        containers = prior.get("containers")
        stopped = receipt.get("containers")
        stop_evidence = receipt.get("stop_evidence")
        if (type(containers) is not dict or set(containers) != OWNED or
                type(stopped) is not dict or set(stopped) != OWNED or
                type(stop_evidence) is not dict or set(stop_evidence) != OWNED or
                set(receipt.get("stop_attempted", [])) != OWNED):
            raise ValueError("PAPER_RESTATEMENT_A151_HELD_IDENTITY_RED")
        for name in OWNED:
            row, installed, stop = containers[name], stopped[name], stop_evidence[name]
            if (type(row) is not dict or type(installed) is not dict or
                    type(stop) is not dict or type(row.get("lifecycle")) is not dict or
                    type(stop.get("after")) is not dict or
                    type(row.get("id")) is not str or
                    re.fullmatch(r"[0-9a-f]{64}", row["id"]) is None or
                    row.get("image") != A143_IMAGE_ID or
                    installed != {"id": row["id"], "image": A143_IMAGE_ID} or
                    row["lifecycle"].get("Status") != "running" or
                    stop.get("before") != row["lifecycle"] or
                    stop["after"].get("Status") != "exited" or
                    type(stop["after"].get("ExitCode")) is not int or
                    stop["after"]["ExitCode"] != 0 or
                    stop.get("classification") not in (
                        "CLEAN_EXIT", "INHERITED_OOM_FLAG_CLEAN_EXIT")):
                raise ValueError("PAPER_RESTATEMENT_A151_HELD_IDENTITY_RED")
    elif type(prior) is dict and prior.get("operator_hold") is not None:
        raise ValueError("PAPER_RESTATEMENT_UNEXPECTED_A151_HELD_PROOF")
    try:
        recorded = datetime.fromisoformat(receipt["recorded_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("PAPER_RESTATEMENT_PRIOR_FAILED_HOST_CLOCK_RED") from exc
    if (recorded.tzinfo is None or recorded.utcoffset() is None or
            recorded > datetime.now(timezone.utc) or
            (quiescence_mode in {"A151_HELD_OPEN", "A151_HELD_STOPPED"} and
             recorded < observed)):
        raise ValueError("PAPER_RESTATEMENT_PRIOR_FAILED_HOST_CLOCK_RED")
    return recorded


def _receipt(path, candidate, quiescence_mode):
    if (path.is_symlink() or not path.is_file() or path.stat().st_size > 65536 or
            path.stat().st_nlink != 1):
        raise ValueError("PAPER_RESTATEMENT_PRIOR_FAILED_HOST_RECEIPT_RED")
    raw = path.read_bytes()
    receipt = json.loads(raw)
    recorded = validate_prior_receipt(receipt, candidate, quiescence_mode)
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
                                     origin["candidate_sha"], origin["quiescence_mode"])
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
    mode = origin.get("quiescence_mode")
    third = origin.get("owner_intraday_comment_id")
    if (mode not in QUIESCENCE_MODES or
            (mode in INTRADAY_MODES and (type(third) is not int or third <= 0 or
                 third in {origin.get("owner_approval_comment_id"),
                           origin.get("owner_deferred_comment_id")})) or
            (mode not in INTRADAY_MODES and third is not None)):
        raise ValueError("PAPER_RESTATEMENT_PRIOR_INTRADAY_OWNER_RED")
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
    decisions = [("owner_approval_comment_id", "RC6_624_APPROVE_DEPLOY_AND_COLD2"),
                 ("owner_deferred_comment_id", "RC6_624_APPROVE_DEPLOY_WITH_HOST_STRESS_DEFERRED")]
    if mode in INTRADAY_MODES:
        decisions.append(("owner_intraday_comment_id", "RC6_624_APPROVE_INTRADAY_SUSPEND_DEPLOY"))
    for field, decision in decisions:
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
