#!/usr/bin/env python3
"""Typed RC6 scheduler jobs on the current verified private Docker release.

The existing system timers retain their calendars. Bootstrap replaces only
reviewed Docker/preopen ExecStart commands with this unprivileged dispatcher.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from scripts.porota_rootless_host import (
    Hold, Host, Identity, DB_RELATIVE, database_snapshot, no_alias,
    read_regular, read_release_state, require,
)

DOCKER_JOBS = {
    "porota-candle-integrity-rc6.service": ("observer", (("python", "/app/rc6_candle_integrity.py"),)),
    "porota-fast-functional-health-rc6.service": ("observer", (("python", "/app/rc6_fast_functional_health.py"),)),
    "porota-market-watch-silence-rc6.service": ("observer", (("python", "-B", "/app/rc6_market_watch_silence.py"),)),
    "porota-full-db-integrity-rc6.service": ("observer", (("python", "/app/rc6_full_db_integrity.py"),)),
    "porota-snapshot-preopen-rc6.service": ("dashboard", (("python", "/app/rc6_snapshot.py", "--phase", "preopen"),)),
    "porota-snapshot-postclose-rc6.service": ("dashboard", (
        ("python", "/app/rc6_snapshot.py", "--phase", "postclose"),
        ("python", "/app/rc6_postclose_review.py"), ("python", "/app/rc6_decision_evidence_report.py"))),
    "porota-local-eod-history-rc6.service": ("observer", (
        ("timeout", "--signal=TERM", "--kill-after=5s", "45s", "nice", "-n", "15", "python", "/app/rc6_local_eod_history.py"),)),
    "porota-introspection-rc6.service": ("observer", (("python", "/app/ops_introspection_rc6.py", "--enqueue-critical"),)),
}
PREOPEN = "porota-preopen-rc6.service"
PUBLISH = "porota-introspection-publish-rc6.service"
INMARKET = "porota-rc6-candle-inmarket-verify.service"
INTROSPECTION = "porota-introspection-rc6.service"
ADAPTED_SERVICES = frozenset((*DOCKER_JOBS, PREOPEN, PUBLISH, INMARKET))
PRESERVED_SERVICES = frozenset({
    "porota-runtime-evidence-rc6.service", "porota-iol-shadow-collector-rc6.service",
    "porota-iol-oauth-health-rc6.service", "porota-byma-morning-watch-rc6.service",
    "porota-scheduler-export-rc6.service",
})
PRESERVED_COMPANIONS = frozenset({
    "porota-critical-approval-rc6.service", "porota-critical-github-proxy-rc6.service",
})

# A stable bootstrap-installed launcher selects source by the validated private
# release SHA. It never evaluates a path supplied by a caller or JSON field.
LAUNCHER = r'''#!/usr/bin/python3
import fcntl,json,os,pathlib,pwd,re,stat,sys
try:
    p=pwd.getpwuid(os.getuid()); assert p.pw_uid>0
    state=pathlib.Path(p.pw_dir)/'.local/state/porota-rc6'
    assert not any(x.is_symlink() for x in (state,*state.parents))
    meta=state.stat(); assert meta.st_uid==p.pw_uid and stat.S_ISDIR(meta.st_mode) and not meta.st_mode&0o077
    lock=os.open(state/'deploy.lock',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    meta=os.fstat(lock); assert stat.S_ISREG(meta.st_mode) and meta.st_uid==p.pw_uid and meta.st_nlink==1 and not meta.st_mode&0o077
    try: fcntl.flock(lock,fcntl.LOCK_SH|fcntl.LOCK_NB)
    except BlockingIOError:
        print(json.dumps({'schema':'porota.rootless.job.v1','state':'SKIPPED','failure_signature':'DEPLOY_IN_PROGRESS'}))
        raise SystemExit(0)
    # Keep lock open through source selection, import, the job and postflight.
    current=state/'current.json'
    fd=os.open(current,os.O_RDONLY|os.O_NOFOLLOW); meta=os.fstat(fd)
    assert stat.S_ISREG(meta.st_mode) and meta.st_uid==p.pw_uid and meta.st_nlink==1 and not meta.st_mode&0o077 and meta.st_size<131072
    raw=os.read(fd,131073); os.close(fd); data=json.loads(raw)
    sha=data.get('source_sha',''); assert re.fullmatch('[0-9a-f]{40}',sha)
    source=state/'releases'/sha/'source'
    for directory in (state/'releases',source.parent,source):
        assert not directory.is_symlink()
        meta=directory.stat(); assert stat.S_ISDIR(meta.st_mode) and meta.st_uid==p.pw_uid and not meta.st_mode&0o022
    sys.path.insert(0,str(source))
    from scripts.porota_rootless_jobs import main
except Exception:
    print(json.dumps({'schema':'porota.rootless.job.v1','state':'BLOQUEADO','failure_signature':'SCHEDULER_LAUNCHER_CUSTODY'}))
    raise SystemExit(79)
raise SystemExit(main())
'''


@contextmanager
def scheduler_lock(identity):
    path = no_alias(identity.state / "deploy.lock")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        meta = os.fstat(fd)
        require(stat.S_ISREG(meta.st_mode) and meta.st_uid == identity.uid and meta.st_nlink == 1 and
                not meta.st_mode & 0o077, "SCHEDULER_LOCK_UNSAFE")
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Hold("DEPLOY_IN_PROGRESS") from exc
        yield
    finally:
        os.close(fd)


def source_directory(identity, current):
    source = no_alias(identity.state / "releases" / current["source_sha"] / "source")
    meta = source.stat()
    require(stat.S_ISDIR(meta.st_mode) and meta.st_uid == identity.uid and not meta.st_mode & 0o022,
            "SCHEDULER_SOURCE_CUSTODY")
    return source


def verified_host(root, identity):
    current, release = read_release_state(identity)
    manager = Host(root, identity)
    require(manager.boundary() == current.get("daemon_id"), "SCHEDULER_DAEMON_CHANGED")
    require(set(current.get("roles", {})) == {"observer", "dashboard"}, "SCHEDULER_PAIR_REQUIRED")
    for role in current["roles"]:
        require(manager.check_role(current, role)["State"].get("Running") is True, "SCHEDULER_RUNTIME_STOPPED")
    database_snapshot(root / DB_RELATIVE)
    return manager, current, release


def execute_commands(manager, current, role, commands, *, timeout=600, classified_critical=False):
    results = []
    for command in commands:
        require(manager.check_role(current, role)["State"].get("Running") is True, "SCHEDULER_RUNTIME_STOPPED")
        if classified_critical:
            # The original introspection unit explicitly accepts exit 2 for a
            # completed CRITICAL report. No other job acquires that exception.
            result = manager.docker.command_result("container", "exec", current["roles"][role], *command, timeout=timeout)
            require(result.returncode in {0, 2}, "SCHEDULER_JOB_FAILED")
            code = result.returncode
        else:
            manager.docker.call("container", "exec", current["roles"][role], *command, timeout=timeout)
            code = 0
        results.append({"role": role, "cid": current["roles"][role], "command": command[0],
                        "exit_code": code, "result": "CLASSIFIED_CRITICAL" if code == 2 else "EXIT_ZERO"})
    return results


def publisher_command(arguments):
    prefix = ("exec", "porota_production_observer", "python", "/app/ops_publish_introspection_rc6.py")
    require(tuple(arguments[:4]) == prefix, "SCHEDULER_DOCKER_BRIDGE_SCOPE")
    choices = {
        "--input-dir": {"/app/data/introspection"}, "--output-dir": {"/app/data/introspection_publish"},
        "--record-status": {"FAILED", "UNCHANGED", "PUBLISHED"}, "--retention-days": {"90"},
    }
    rest = list(arguments[4:])
    seen = set()
    while rest:
        key = rest.pop(0)
        require(key not in seen, "SCHEDULER_DOCKER_BRIDGE_SCOPE")
        seen.add(key)
        if key == "--prune-only":
            continue
        require(rest, "SCHEDULER_DOCKER_BRIDGE_SCOPE")
        value = rest.pop(0)
        require((key in choices and value in choices[key]) or
                key == "--day" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) or
                key == "--detail" and re.fullmatch(r"PUBLISHER_EXIT_\d{1,3}", value),
                "SCHEDULER_DOCKER_BRIDGE_SCOPE")
    require("--output-dir" in seen, "SCHEDULER_DOCKER_BRIDGE_SCOPE")
    return tuple(arguments[2:])


def run_job(unit, *, root=Path("/opt/porota-trading"), identity=None, run=subprocess.run):
    identity = identity or Identity.current()
    require(unit in ADAPTED_SERVICES, "SCHEDULER_UNIT_SCOPE")
    with scheduler_lock(identity):
        manager, current, _ = verified_host(root, identity)
        if unit in DOCKER_JOBS:
            role, commands = DOCKER_JOBS[unit]
            commands_result = execute_commands(manager, current, role, commands,
                timeout=900 if unit == "porota-full-db-integrity-rc6.service" else 600,
                classified_critical=unit == INTROSPECTION)
        elif unit == INMARKET:
            baseline_path = identity.state / "inmarket-baseline-20260917"
            if not baseline_path.exists():
                baseline_path = Path("/var/lib/porota/rc6-candle-inmarket-baseline-20260917")
            baseline = read_regular(baseline_path, 128).decode().strip()
            require(re.fullmatch(r"[0-9]{1,20}", baseline), "INMARKET_BASELINE_UNVERIFIED")
            commands_result = execute_commands(manager, current, "observer", (("python",
                "/app/scripts/rc6_candle_inmarket_evidence.py", "--baseline-cursor", baseline,
                "--summary-path", "/app/data/validation/candle_inmarket_latest.json"),))
        else:
            source = source_directory(identity, current)
            env = identity.environment()
            env.update({"POROTA_RUNTIME_BACKEND": "ROOTLESS", "PYTHONDONTWRITEBYTECODE": "1",
                        "PYTHONPATH": str(source), "PAPER_V17_DB_PATH": str(root / DB_RELATIVE),
                        "DOCKER_HOST": f"unix://{identity.socket}"})
            if unit == PREOPEN:
                command = ("/usr/bin/python3", "-B", str(source / "rc6_preopen.py"), "--publish-paper-gate")
            else:
                # Only this unchanged publisher uses bare 'docker'. The shim
                # accepts its exact local formatting/status commands above.
                env["PATH"] = str(identity.state / "bin") + ":" + env["PATH"]
                command = ("/bin/sh", str(source / "scripts/porota_publish_introspection_github_rc6.sh"))
            result = run(command, cwd=root, env=env, check=False, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, timeout=600)
            require(result.returncode == 0, "SCHEDULER_HOST_JOB_FAILED")
            commands_result = [{"executor": "HOST_SSH_UID", "result": "EXIT_ZERO"}]
        database_snapshot(root / DB_RELATIVE)
        return {"schema": "porota.rootless.job.v1", "unit": unit, "state": "EXECUTED",
                "source_sha": current["source_sha"], "image_id": current["image_id"],
                "real_orders_sent": 0, "commands": commands_result,
                "exit_code": 2 if any(row.get("exit_code") == 2 for row in commands_result) else 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unit", choices=sorted(ADAPTED_SERVICES))
    parser.add_argument("--docker-bridge", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        identity = Identity.current()
        if args.docker_bridge is not None:
            command = publisher_command(args.docker_bridge)
            with scheduler_lock(identity):
                manager, current, _ = verified_host(Path("/opt/porota-trading"), identity)
                execute_commands(manager, current, "observer", (command,))
            return 0
        require(args.unit is not None, "SCHEDULER_UNIT_REQUIRED")
        result = run_job(args.unit, identity=identity)
        print(json.dumps(result, sort_keys=True))
        return result["exit_code"]
    except Exception as exc:
        reason = str(exc) if isinstance(exc, Hold) else "UNCLASSIFIED_SCHEDULER_FAILURE"
        print(json.dumps({"schema": "porota.rootless.job.v1", "state": "SKIPPED" if reason == "DEPLOY_IN_PROGRESS" else "BLOQUEADO",
                          "failure_signature": reason, "unit": args.unit}))
        return 0 if reason == "DEPLOY_IN_PROGRESS" else 79


if __name__ == "__main__":
    raise SystemExit(main())
