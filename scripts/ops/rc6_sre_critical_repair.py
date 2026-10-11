"""Bounded RC6 PAPER critical unit plan/apply; invoked only by pinned Actions SSH.

The host script never reads secret values. Apply needs a sealed plan from a
separate read-only run, live owner admission on the runner and the deploy mutex.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import time

UNIT = Path("/etc/systemd/system/porota-critical-approval-rc6.service")
DB = "/opt/porota-trading/data/paper_v17/observer_v17.db"
NAME = "porota_critical_approval_rc6"
SIBLINGS = ("porota_production_observer", "porota_production_dashboard")
HEALTH = (
    '--init --pids-limit 64 --memory 192m '
    '--health-cmd="python -c \'from fm_critical_approval_unix_runtime_rc6 '
    'import UnixGithubIssuesClient; h=UnixGithubIssuesClient().health(); '
    'assert h.get(\\"status\\")==\\"ok\\" and '
    'h.get(\\"capability\\")==\\"issues_only\\"\'" '
    '--health-interval=30s --health-timeout=20s '
    '--health-retries=3 --health-start-period=20s'
)
OLD = "--pids-limit 64 --memory 192m"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def run(*args: str, timeout: int = 20) -> str:
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=True)
    return p.stdout.strip()


def patch_unit(raw: bytes) -> bytes:
    text = raw.decode("utf-8")
    lines = text.splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines)
               if line.startswith("ExecStart=/usr/bin/docker run ")]
    if len(matches) != 1:
        raise RuntimeError("UNIT_EXECSTART_COUNT")
    index = matches[0]
    line = lines[index]
    if line.count(OLD) != 1 or "--init" in line or "--health-cmd" in line:
        flags = {
            "old_exact": line.count(OLD),
            "pids": re.findall(r"--pids-limit(?:=|\s+)([0-9]+)", line),
            "memory": re.findall(r"--memory(?:=|\s+)([0-9]+[a-zA-Z]*)", line),
            "init": "--init" in line,
            "health_override": "--health-cmd" in line,
            "health_broker_client": "UnixGithubIssuesClient" in line,
            "health_socket": "github.sock" in line,
            "health_http": "http" in line.lower(),
            "health_dashboard": "DASHBOARD_PORT" in line,
            "line_sha256": sha(line.encode()),
        }
        raise RuntimeError("UNIT_SOURCE_MISMATCH:" + json.dumps(flags, sort_keys=True))
    if not all(token in line for token in
               ("--read-only", "--user botuser", "--cap-drop ALL",
                "POROTA_RUNTIME_MODE=PRODUCTION_PAPER", "--pull never")):
        raise RuntimeError("UNIT_PAPER_GUARD_MISSING")
    lines[index] = line.replace(OLD, HEALTH, 1)
    return "".join(lines).encode("utf-8")


def inspect(name: str) -> dict:
    data = json.loads(run("docker", "inspect", name, timeout=25))[0]
    state = data["State"]
    return {
        "id": data["Id"], "image_id": data["Image"],
        "running": bool(state["Running"]), "restart_count": data["RestartCount"],
        "health": (state.get("Health") or {}).get("Status"),
        "pids_limit": data["HostConfig"]["PidsLimit"],
        "init": data["HostConfig"].get("Init"),
        "pid": state.get("Pid"), "oom_killed": bool(state["OOMKilled"]),
        "health_cmd_class": (
            "BROKER_ISSUES" if "UnixGithubIssuesClient" in
            str((data["Config"].get("Healthcheck") or {}).get("Test") or "")
            else "OTHER"
        ),
    }


def paper_state() -> list:
    c = sqlite3.connect("file:" + DB + "?mode=ro", uri=True, timeout=2)
    try:
        c.execute("PRAGMA query_only=ON")
        return list(c.execute(
            "SELECT mode,real_orders_sent FROM observer_state WHERE id=1"
        ).fetchone())
    finally:
        c.close()


def snapshot() -> dict:
    stat = os.statvfs("/")
    return {
        "utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "free_bytes": stat.f_bavail * stat.f_frsize,
        "free_inodes": stat.f_ffree,
        "paper": paper_state(),
        "critical": inspect(NAME),
        "siblings": {name: inspect(name) for name in SIBLINGS},
        "unit_sha256": sha(UNIT.read_bytes()),
        "unit_inode": UNIT.stat().st_ino,
        "unit_mode": UNIT.stat().st_mode & 0o7777,
    }


def plan(tool_sha256: str) -> dict:
    raw = UNIT.read_bytes()
    before = snapshot()
    if before["paper"] != ["PRODUCTION_PAPER", 0]:
        raise RuntimeError("PAPER_GUARD_RED")
    if before["critical"]["health"] != "unhealthy":
        raise RuntimeError("CRITICAL_CHANGED_REPLAN")
    if not before["critical"]["running"] or not all(
        v["running"] for v in before["siblings"].values()
    ):
        raise RuntimeError("SERVICE_STATE_RED")
    if before["unit_mode"] != 0o644 or UNIT.is_symlink():
        raise RuntimeError("UNIT_METADATA_RED")
    patched = patch_unit(raw)
    plan_doc = {
        "schema": "porota.rc6.sre.critical-unit-plan.v1",
        "actor": "WORK_RC6_SRE_20261011_1740Z",
        "mutation": "SINGLE_CRITICAL_UNIT_INIT_AND_BROKER_HEALTH_RESTART",
        "unit": str(UNIT), "old_sha256": sha(raw),
        "new_sha256": sha(patched), "old_inode": before["unit_inode"],
        "old_mode": before["unit_mode"], "before": before,
        "do_not_change": ["observer", "dashboard", "PPI Watch", "PAPER DB"],
        "no_candidate_image_deploy": True,
        "source_file_sha256": tool_sha256,
    }
    return plan_doc


def diagnose() -> dict:
    raw = UNIT.read_bytes()
    lines = [line for line in raw.decode("utf-8").splitlines()
             if line.startswith("ExecStart=/usr/bin/docker run ")]
    if len(lines) != 1:
        raise RuntimeError("UNIT_EXECSTART_COUNT")
    line = lines[0]
    docker = json.loads(run("docker", "inspect", NAME, timeout=25))[0]
    test = str((docker["Config"].get("Healthcheck") or {}).get("Test") or "")
    return {
        "schema": "porota.rc6.sre.critical-unit-diagnose.v1",
        "unit_sha256": sha(raw),
        "line_sha256": sha(line.encode()),
        "unit_flags": {
            "init": "--init" in line,
            "pids": re.findall(r"--pids-limit(?:=|\s+)([0-9]+)", line),
            "health_override": "--health-cmd" in line,
            "broker_client": "UnixGithubIssuesClient" in line,
            "socket": "github.sock" in line,
            "http": "http" in line.lower(),
            "dashboard": "DASHBOARD_PORT" in line,
        },
        "live_test_sha256": sha(test.encode()),
        "live_test_categories": {
            "broker_client": "UnixGithubIssuesClient" in test,
            "socket": "github.sock" in test,
            "http": "http" in test.lower(),
            "dashboard": "DASHBOARD_PORT" in test,
            "python": "python" in test,
        },
        "before": snapshot(),
    }


def apply(expected: dict, tool_sha256: str) -> dict:
    current = plan(tool_sha256)
    if not all(current[key] == expected.get(key) for key in
               ("schema", "actor", "mutation", "unit", "old_sha256",
                "new_sha256", "old_inode", "old_mode", "source_file_sha256")):
        raise RuntimeError("PLAN_IDENTITY_CHANGED")
    old = expected["before"]
    fresh = current["before"]
    if (fresh["paper"] != old["paper"]
            or fresh["critical"]["id"] != old["critical"]["id"]
            or fresh["critical"]["image_id"] != old["critical"]["image_id"]
            or fresh["critical"]["restart_count"] != old["critical"]["restart_count"]
            or any(fresh["siblings"][name]["id"] != old["siblings"][name]["id"]
                   or fresh["siblings"][name]["restart_count"] !=
                   old["siblings"][name]["restart_count"] for name in SIBLINGS)):
        raise RuntimeError("PLAN_PRESTATE_CHANGED")
    raw = UNIT.read_bytes()
    if sha(raw) != expected["old_sha256"] or UNIT.stat().st_ino != expected["old_inode"]:
        raise RuntimeError("UNIT_RACE")
    patched = patch_unit(raw)
    if sha(patched) != expected["new_sha256"]:
        raise RuntimeError("PLAN_PATCH_DRIFT")
    before = current["before"]
    # Record preconditions before a single atomic unit replacement.
    fd, target = tempfile.mkstemp(prefix=".porota-critical-sre-", dir=str(UNIT.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(patched)
            f.flush()
            os.fchmod(f.fileno(), 0o644)
            os.fsync(f.fileno())
        os.replace(target, UNIT)
        dirfd = os.open(str(UNIT.parent), os.O_DIRECTORY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    finally:
        if os.path.lexists(target):
            os.unlink(target)
    run("systemctl", "daemon-reload", timeout=20)
    run("systemctl", "restart", "porota-critical-approval-rc6.service", timeout=70)
    observations = []
    for _ in range(9):
        time.sleep(10)
        v = inspect(NAME)
        observations.append({
            "utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "health": v["health"], "restart_count": v["restart_count"],
            "pids_limit": v["pids_limit"], "init": v["init"],
            "health_cmd_class": v["health_cmd_class"],
        })
        if v["health"] == "healthy":
            break
    after = snapshot()
    good = (
        after["unit_sha256"] == expected["new_sha256"]
        and after["paper"] == ["PRODUCTION_PAPER", 0]
        and after["critical"]["health"] == "healthy"
        and after["critical"]["health_cmd_class"] == "BROKER_ISSUES"
        and after["critical"]["init"] is True
        and after["critical"]["pids_limit"] == 64
        and after["critical"]["image_id"] == before["critical"]["image_id"]
        and after["critical"]["restart_count"] == 0
        and all(after["siblings"][name]["id"] == before["siblings"][name]["id"]
                and after["siblings"][name]["restart_count"] ==
                before["siblings"][name]["restart_count"] for name in SIBLINGS)
    )
    return {"schema": "porota.rc6.sre.critical-unit-apply.v1",
            "plan_sha256": sha(json.dumps(expected, sort_keys=True).encode()),
            "before": before, "after": after,
            "health_observations": observations, "validated": good,
            "fail_closed": not good}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("diagnose", "plan", "apply"), required=True)
    parser.add_argument("--plan-base64")
    parser.add_argument("--tool-sha256", required=True)
    args = parser.parse_args()
    if args.mode == "diagnose":
        result = diagnose()
    elif args.mode == "plan":
        result = plan(args.tool_sha256)
    else:
        if not args.plan_base64:
            raise RuntimeError("PLAN_REQUIRED")
        raw = base64.b64decode(args.plan_base64, validate=True)
        result = apply(json.loads(raw), args.tool_sha256)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    if args.mode == "apply" and not result["validated"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
