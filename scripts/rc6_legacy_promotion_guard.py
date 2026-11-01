"""Fail-closed source/CI proof for the legacy manual promotion workflow.

This does not authorize RC6 Deploy V2 or inspect the productive host.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SHA = re.compile(r"[0-9a-f]{40}\Z")
BRANCHES = {"testing", "develop"}
REPO = "mbalbo2023/Porota-trading"


class PromotionBlocked(ValueError):
    pass


def source_package(branch, sha, tree_output):
    if branch not in BRANCHES or not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise PromotionBlocked("PROMOTION_SOURCE_IDENTITY_INVALID")
    rows = {}
    for line in tree_output.splitlines():
        try:
            header, path = line.split("\t", 1)
            mode, kind, blob = header.split(" ")
        except ValueError:
            raise PromotionBlocked("PROMOTION_SOURCE_TREE_INVALID") from None
        if path in rows or path not in {"requirements.txt", "entrypoint.py"}:
            raise PromotionBlocked("PROMOTION_SOURCE_TREE_INVALID")
        rows[path] = mode, kind, blob
    if set(rows) != {"requirements.txt", "entrypoint.py"} or any(
            mode not in {"100644", "100755"} or kind != "blob" or not SHA.fullmatch(blob)
            for mode, kind, blob in rows.values()):
        raise PromotionBlocked("PROMOTION_APPLICATION_PACKAGE_REQUIRED")


def _successful_step(job, name):
    return any(step.get("name") == name and step.get("conclusion") == "success"
               for step in job.get("steps", []) if isinstance(step, dict))


def latest_exact_ci(runs, branch, sha):
    if branch not in BRANCHES or not SHA.fullmatch(sha):
        raise PromotionBlocked("PROMOTION_SOURCE_IDENTITY_INVALID")
    if not isinstance(runs, dict) or not isinstance(runs.get("workflow_runs"), list):
        raise PromotionBlocked("PROMOTION_CI_RUNS_INVALID")
    candidates = [run for run in runs["workflow_runs"] if isinstance(run, dict)
                  and run.get("head_sha") == sha and run.get("head_branch") == branch
                  and run.get("event") == "push"
                  and type(run.get("id")) is int and run["id"] > 0]
    if not candidates:
        raise PromotionBlocked("PROMOTION_EXACT_PUSH_CI_MISSING")
    selected = max(candidates, key=lambda run: run["id"])
    if selected.get("status") != "completed" or selected.get("conclusion") != "success":
        raise PromotionBlocked("PROMOTION_LATEST_EXACT_CI_RED")
    return selected


def application_ci_proof(runs, jobs, branch, sha):
    selected = latest_exact_ci(runs, branch, sha)
    if (not isinstance(jobs, dict) or type(jobs.get("total_count")) is not int
            or not 1 <= jobs["total_count"] <= 100
            or not isinstance(jobs.get("jobs"), list)
            or len(jobs["jobs"]) != jobs["total_count"]
            or any(not isinstance(job, dict) for job in jobs["jobs"])):
        raise PromotionBlocked("PROMOTION_CI_JOBS_INCOMPLETE")
    tests = [job for job in jobs["jobs"] if job.get("name") == "Suite de tests"
             and job.get("run_id") == selected["id"] and job.get("conclusion") == "success"]
    builds = [job for job in jobs["jobs"] if job.get("name") == "Build y arranque"
              and job.get("run_id") == selected["id"] and job.get("conclusion") == "success"]
    if (len(tests) != 1 or len(builds) != 1
            or not _successful_step(tests[0], "Ejecutar la suite")
            or not _successful_step(tests[0], "Cobertura minima en los modulos que tocan dinero")
            or not _successful_step(builds[0], "Build")
            or not _successful_step(builds[0], "Levantar el stack")
            or not _successful_step(builds[0], "El panel responde")):
        raise PromotionBlocked("PROMOTION_APPLICATION_CI_NOT_EXECUTED")
    return selected["id"]


def _fetch(path, token):
    request = Request("https://api.github.com/repos/" + REPO + path,
                      headers={"Authorization": "Bearer " + token,
                               "Accept": "application/vnd.github+json",
                               "X-GitHub-Api-Version": "2022-11-28"})
    with urlopen(request, timeout=12) as response:
        body = response.read(2_000_001)
    if len(body) > 2_000_000:
        raise PromotionBlocked("PROMOTION_CI_API_BUDGET_EXCEEDED")
    return json.loads(body)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 3 and argv[0] == "source":
        _, branch, sha = argv
        tree = subprocess.check_output(
            ["git", "ls-tree", sha, "--", "requirements.txt", "entrypoint.py"],
            text=True, timeout=8)
        source_package(branch, sha, tree)
        print("PROMOTION_SOURCE=APPLICATION|source_sha=" + sha)
        return 0
    if argv == ["ci"]:
        branch, sha = os.environ.get("SOURCE_BRANCH", ""), os.environ.get("SOURCE_SHA", "")
        token = os.environ.get("GH_TOKEN", "")
        if branch not in BRANCHES or not SHA.fullmatch(sha) or not token:
            raise PromotionBlocked("PROMOTION_CI_INPUT_INVALID")
        query = urlencode({"branch": branch, "event": "push", "per_page": 30})
        runs = _fetch("/actions/workflows/ci.yml/runs?" + query, token)
        run_id = latest_exact_ci(runs, branch, sha)["id"]
        jobs = _fetch(f"/actions/runs/{run_id}/jobs?per_page=100&filter=latest", token)
        proven = application_ci_proof(runs, jobs, branch, sha)
        print(f"PROMOTION_CI=APPLICATION_TESTED|source_sha={sha}|run_id={proven}")
        return 0
    raise PromotionBlocked("PROMOTION_COMMAND_INVALID")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (PromotionBlocked, subprocess.SubprocessError, OSError, ValueError) as error:
        reason = str(error) if isinstance(error, PromotionBlocked) else "PROMOTION_PROOF_UNAVAILABLE"
        print("::error::" + reason, file=sys.stderr)
        raise SystemExit(2) from None
