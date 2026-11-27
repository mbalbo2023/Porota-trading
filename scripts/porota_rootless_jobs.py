#!/usr/bin/env python3
"""Typed RC6 scheduler jobs on the current verified private Docker release.

The existing system timers retain their calendars. Bootstrap replaces only
reviewed Docker/preopen ExecStart commands with this unprivileged dispatcher.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
from shutil import disk_usage
import stat
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from rc6_paper_admission_gate import iol_recovery_due
from rc6_validation_runtime import PROOF_SOURCE
from co_market_sessions_hf6 import byma_paper_spot_phase
from scripts.porota_rootless_host import (
    Hold, Host, Identity, DB_RELATIVE, database_snapshot, no_alias,
    read_regular, read_release_state, require, user_lock,
)

POSTCLOSE = "porota-snapshot-postclose-rc6.service"

DOCKER_JOBS = {
    "porota-candle-integrity-rc6.service": ("observer", (("python", "/app/rc6_candle_integrity.py"),)),
    "porota-fast-functional-health-rc6.service": ("observer", (("python", "/app/rc6_fast_functional_health.py"),)),
    "porota-market-watch-silence-rc6.service": ("observer", (("python", "-B", "/app/rc6_market_watch_silence.py"),)),
    "porota-full-db-integrity-rc6.service": ("observer", (("python", "/app/rc6_full_db_integrity.py"),)),
    "porota-snapshot-preopen-rc6.service": ("dashboard", (("python", "/app/rc6_snapshot.py", "--phase", "preopen"),)),
    POSTCLOSE: ("dashboard", (
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
IOL_COLLECTOR = "porota-iol-shadow-collector-rc6.service"
IOL_HEALTH = "porota-iol-oauth-health-rc6.service"
IOL_JOBS = frozenset({IOL_COLLECTOR, IOL_HEALTH})
ADAPTED_SERVICES = frozenset((*DOCKER_JOBS, PREOPEN, PUBLISH, INMARKET))
PRESERVED_SERVICES = frozenset({
    "porota-runtime-evidence-rc6.service", "porota-iol-shadow-collector-rc6.service",
    "porota-iol-oauth-health-rc6.service", "porota-byma-morning-watch-rc6.service",
    "porota-scheduler-export-rc6.service",
})
PRESERVED_COMPANIONS = frozenset({
    "porota-critical-approval-rc6.service", "porota-critical-github-proxy-rc6.service",
})

# Relay only fixed check names and BYMA reason codes. The child receipt may
# contain runtime or provider details that must stay out of journald.
PREOPEN_RED_KEYS = frozenset((
    "release_identity", "observer_container", "dashboard_container",
    "observer_db", "disk", "required_rc6_timers", "byma_morning_watch",
    "iol_reference_fallback", "history_quarantine", "foreign_market_policy",
    "paper_admission_receipt",
))
PREOPEN_BYMA_RED_REASONS = frozenset((
    "BYMA_WATCH_TIMESTAMP_NAIVE", "BYMA_WATCH_TIMESTAMP_FUTURE",
    "BYMA_PENDING_REVIEW_INVALID", "BYMA_AUTHORITY_CHANGE_REVIEW_REQUIRED",
    "BYMA_WATCH_NOT_TODAY", "BYMA_DRY_VALIDATION_LATEST_NOT_SAFE",
    "BYMA_WATCH_DEGRADED", "BYMA_WATCH_STATE_UNKNOWN", "BYMA_WATCH_UNAVAILABLE",
))
PREOPEN_CAPTURE_LIMIT = 1024 * 1024


def _closed_report_receipt(unit, current, phase):
    return {"schema": "porota.rootless.job.v1", "unit": unit, "state": "SKIPPED",
            "source_sha": current["source_sha"], "image_id": current["image_id"],
            "reason": "REPORT_OUTSIDE_PAPER_WHEEL", "phase": phase, "exit_code": 0}

# A stable bootstrap-installed launcher selects source by the validated private
# release SHA. It never evaluates a path supplied by a caller or JSON field.
LAUNCHER = r'''#!/usr/bin/python3
import fcntl,json,os,pathlib,pwd,re,stat,sys
stage='IDENTITY'
try:
    p=pwd.getpwuid(os.getuid()); assert p.pw_uid>0
    stage='STATE_CUSTODY'
    state=pathlib.Path(p.pw_dir)/'.local/state/porota-rc6'
    assert not any(x.is_symlink() for x in (state,*state.parents))
    meta=state.stat(); assert meta.st_uid==p.pw_uid and stat.S_ISDIR(meta.st_mode) and not meta.st_mode&0o077
    stage='DEPLOY_LOCK'
    lock=os.open(state/'deploy.lock',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    meta=os.fstat(lock); assert stat.S_ISREG(meta.st_mode) and meta.st_uid==p.pw_uid and meta.st_nlink==1 and not meta.st_mode&0o077
    try: fcntl.flock(lock,fcntl.LOCK_SH|fcntl.LOCK_NB)
    except BlockingIOError:
        print(json.dumps({'schema':'porota.rootless.job.v1','state':'SKIPPED','failure_signature':'DEPLOY_IN_PROGRESS'}))
        raise SystemExit(0)
    # Keep lock open through source selection, import, the job and postflight.
    stage='CURRENT_RELEASE'
    current=state/'current.json'
    fd=os.open(current,os.O_RDONLY|os.O_NOFOLLOW); meta=os.fstat(fd)
    assert stat.S_ISREG(meta.st_mode) and meta.st_uid==p.pw_uid and meta.st_nlink==1 and not meta.st_mode&0o077 and meta.st_size<131072
    raw=os.read(fd,131073); os.close(fd); data=json.loads(raw)
    sha=data.get('source_sha',''); assert re.fullmatch('[0-9a-f]{40}',sha)
    stage='SOURCE_CUSTODY'
    source=state/'releases'/sha/'source'
    for directory in (state/'releases',source.parent,source):
        assert not directory.is_symlink()
        meta=directory.stat(); assert stat.S_ISDIR(meta.st_mode) and meta.st_uid==p.pw_uid and not meta.st_mode&0o022
    sys.path.insert(0,str(source))
    stage='SOURCE_IMPORT'
    from scripts.porota_rootless_jobs import main
except Exception:
    print(json.dumps({'schema':'porota.rootless.job.v1','state':'BLOQUEADO','failure_signature':'SCHEDULER_LAUNCHER_CUSTODY','failure_stage':stage}))
    raise SystemExit(1)
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


def publish_runtime_evidence(identity, *, root=Path("/opt/porota-trading"), run=subprocess.run):
    """Run the existing read-only publisher from the verified release source."""
    with scheduler_lock(identity):
        current, _ = read_release_state(identity)
        phase = byma_paper_spot_phase(datetime.now(timezone.utc))
        if phase != "OPEN":
            return _closed_report_receipt("porota-runtime-evidence-rc6.service", current, phase)
        source = source_directory(identity, current)
        script = source / "scripts/porota_publish_runtime_evidence_github_rc6.sh"
        environment = identity.environment()
        environment.update(POROTA_BUILD_SHA=current["source_sha"],
                           POROTA_CANDIDATE_TREE_SHA=current["source_tree"])
        result = run(("/bin/sh", str(script), "--verified-release"),
                     cwd=root, env=environment, timeout=150, check=False)
        require(result.returncode == 0, "RUNTIME_EVIDENCE_PUBLISH_FAILED")


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


def _read_preopen_child(output):
    try:
        output.flush()
        size = output.tell()
        if not 0 < size <= PREOPEN_CAPTURE_LIMIT:
            return None
        output.seek(0)
        receipt = json.loads(output.read(PREOPEN_CAPTURE_LIMIT))
        return receipt if type(receipt) is dict and receipt.get("schema") == "POROTA_RC6_PREOPEN_V3" else None
    except (OSError, ValueError, TypeError, UnicodeDecodeError):
        return None


def read_preopen_red_receipt(output):
    """Read the allowlisted portion of a bounded RED child receipt."""
    receipt = _read_preopen_child(output)
    if receipt is None or receipt.get("status") != "RED":
        return None
    try:
        names = receipt.get("red_checks")
        if (type(names) is not list or not 1 <= len(names) <= len(PREOPEN_RED_KEYS) or
                any(type(name) is not str or name not in PREOPEN_RED_KEYS for name in names) or
                len(set(names)) != len(names)):
            return None
        summary = {"preopen_red_checks": names}
        checks = receipt.get("checks")
        byma = checks.get("byma_morning_watch") if type(checks) is dict else None
        if "byma_morning_watch" in names and type(byma) is dict and byma.get("state") == "RED":
            reason = byma.get("reason")
            if type(reason) is str and reason in PREOPEN_BYMA_RED_REASONS:
                summary["preopen_red_reasons"] = {"byma_morning_watch": reason}
        return summary
    except (OSError, ValueError, TypeError, UnicodeDecodeError):
        return None


def read_release_revalidation(output, code):
    result = _read_preopen_child(output)
    require(result is not None and code in {0, 2}, "PAPER_RELEASE_REVALIDATION_CHILD_UNVERIFIED")
    gate = result.get("paper_admission_receipt")
    require(type(gate) is dict, "PAPER_RELEASE_REVALIDATION_CHILD_UNVERIFIED")
    status, reason = gate.get("status"), gate.get("reason")
    require(status in ("GO_PAPER_SCALPING", "NO_GO_PAPER_SCALPING")
            and (status != "GO_PAPER_SCALPING" or code == 0 and result.get("status") == "GREEN")
            and isinstance(reason, str) and re.fullmatch(r"[A-Z_]{1,96}", reason),
            "PAPER_RELEASE_REVALIDATION_CHILD_UNVERIFIED")
    return {"source": "PAPER_RELEASE_REVALIDATION", "result": status, "reason": reason, "exit_code": code}


def publish_validation(manager, current, root, identity):
    """Pass the already verified rootless identity to the fixed container command."""
    health = current.get("health") or {}
    controller = current.get("controller") or {}
    proof = {
        "source": PROOF_SOURCE, "observed_at": datetime.now(timezone.utc).isoformat(),
        "candidate_sha": current.get("source_sha"), "candidate_tree_sha": current.get("source_tree"),
        "deployment_sha": current.get("deployment_sha"), "image_id": current.get("image_id"),
        "download_receipt_sha256": current.get("download_receipt_sha256"),
        "orders_guard": health.get("orders_guard"), "rootless_pair": True,
        "startup_verified": (health.get("observer") == "running" and health.get("dashboard") == "healthy"
                             and controller.get("status") == "READY"
                             and controller.get("source_sha") == current.get("source_sha")
                             and controller.get("source_tree") == current.get("source_tree")),
        "data_free_bytes": disk_usage(root / "data").free,
        "docker_free_bytes": disk_usage(identity.store).free,
    }
    cid = current["roles"]["dashboard"]
    raw = manager.docker.call("container", "exec", cid, "python", "-B", "/app/rc6_validation_publish.py",
                              "--runtime-proof", json.dumps(proof, separators=(",", ":")), timeout=60)
    receipt = json.loads(raw)
    state = receipt.get("state")
    require(state in {"PUBLISHED", "UNCHANGED", "NOT_DUE"}, "VALIDATION_PUBLICATION_UNVERIFIED")
    entry = {"role": "dashboard", "cid": cid, "command": "validation_publish", "exit_code": 0,
             "result": state}
    if state != "NOT_DUE":
        day, records = receipt.get("session_date"), receipt.get("records")
        require(isinstance(day, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day)
                and type(records) is int and records > 0, "VALIDATION_PUBLICATION_UNVERIFIED")
        entry.update(session_date=day, records=records)
    return entry


def run_iol_job(unit, root, identity, current, *, verify_rest=False, run=subprocess.run):
    from iol_rest_readonly_rc6 import IOLRestError, IOLRestReadOnly, SOURCE
    from scripts.rc6_source_cycle_dispatch import phase_status, run_cycle

    db = root / DB_RELATIVE
    sealed_db = current.get("database")
    require(sealed_db == database_snapshot(db), "IOL_RELEASE_DATABASE_IDENTITY_UNVERIFIED")
    if unit == IOL_COLLECTOR:
        env = {**identity.environment(), "POROTA_IOL_SHADOW_MODE": "OBSERVE_ONLY",
               "POROTA_IOL_SHADOW_SCOPE": "AUDIT_ALL", "PYTHONDONTWRITEBYTECODE": "1",
               "PYTHONPATH": str(source_directory(identity, current))}
        cycle = run_cycle(db=db, market_root=root / "data/market", lock=identity.state / "iol-source-cycle.lock",
                          release_database=sealed_db, environment=env, runner=run)
        code = 0 if cycle["status"] in {"NOT_DUE", "COMPLETE"} else 2
        return {"source": SOURCE, "source_cycle": cycle, "exit_code": code}

    late, _ = phase_status(datetime.now(timezone.utc), "late_source")
    client = IOLRestReadOnly(identity.state / "iol-rest", config=root / ".env", allow_renew=not late)
    try:
        client.authenticate()
        if verify_rest:
            asset = client.call("get_asset_info", {"symbol": "GD30", "market": "BCBA"})
            require(asset["type"] == "BONOS" and asset["currency"] == "ARS" and asset["term"] == "T1",
                    "IOL_REST_REFERENCE_IDENTITY_UNVERIFIED")
    except IOLRestError as exc:
        return {"source": SOURCE, "result": str(exc), "http_status": exc.status_code, "exit_code": 2}
    return {"source": SOURCE, "result": "REFERENCE_GD30_VERIFIED" if verify_rest else "AUTHENTICATED", "exit_code": 0}


def host_job_environment(root, identity, current, source):
    env = identity.environment()
    env.update({"POROTA_RUNTIME_BACKEND": "ROOTLESS", "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(source), "PAPER_V17_DB_PATH": str(root / DB_RELATIVE),
                "DOCKER_HOST": f"unix://{identity.socket}"})
    if current.get("market_provider") == "IOL_REST":
        env["POROTA_RC6_MARKET_PROVIDER"] = "IOL_REST"
    return env


def run_paper_revalidation(root, identity, current, run, *, timeout):
    db = root / DB_RELATIVE
    require(database_snapshot(db) == current.get("database"), "PAPER_RELEASE_DATABASE_IDENTITY_UNVERIFIED")
    source = source_directory(identity, current)
    env = host_job_environment(root, identity, current, source)
    command = ("/usr/bin/python3", "-B", str(source / "rc6_preopen.py"),
               "--revalidate-release", current["source_sha"])
    try:
        with user_lock(identity.state / "paper-preopen-revalidation.lock"):
            with tempfile.TemporaryFile(dir=identity.state) as output:
                result = run(command, cwd=root, env=env, check=False, stdout=output,
                             stderr=subprocess.DEVNULL, timeout=timeout)
                receipt = read_release_revalidation(output, result.returncode)
            require(database_snapshot(db) == current.get("database"), "PAPER_RELEASE_DATABASE_IDENTITY_CHANGED")
            return receipt
    except Hold as error:
        if str(error) != "OWNER_BUSY":
            raise
        return {"source": "PAPER_RELEASE_REVALIDATION", "result": "NO_GO_PAPER_SCALPING",
                "reason": "PAPER_RELEASE_REVALIDATION_IN_PROGRESS", "exit_code": 2}


def run_job(unit, *, root=Path("/opt/porota-trading"), identity=None, verify_rest=False,
            revalidate_paper_gate=False, run=subprocess.run):
    identity = identity or Identity.current()
    require(unit in ADAPTED_SERVICES | IOL_JOBS and (not verify_rest or unit == IOL_HEALTH)
            and (not revalidate_paper_gate or unit == PREOPEN), "SCHEDULER_UNIT_SCOPE")
    with scheduler_lock(identity):
        if unit in {INTROSPECTION, PUBLISH}:
            phase = byma_paper_spot_phase(datetime.now(timezone.utc))
            if phase != "OPEN":
                current, _ = read_release_state(identity)
                return _closed_report_receipt(unit, current, phase)
        manager, current, _ = verified_host(root, identity)
        preopen_failure = None
        paper_recovery = None
        if revalidate_paper_gate:
            commands_result = [run_paper_revalidation(root, identity, current, run, timeout=120)]
        elif unit in DOCKER_JOBS:
            role, commands = DOCKER_JOBS[unit]
            commands_result = execute_commands(manager, current, role, commands,
                timeout=900 if unit == "porota-full-db-integrity-rc6.service" else 600,
                classified_critical=unit == INTROSPECTION)
            if unit == POSTCLOSE:
                commands_result.append(publish_validation(manager, current, root, identity))
        elif unit in IOL_JOBS:
            result = run_iol_job(unit, root, identity, current, verify_rest=verify_rest, run=run)
            commands_result = [result]
            if (unit == IOL_HEALTH and not verify_rest and current.get("market_provider") == "IOL_REST"
                    and result["exit_code"] == 0 and iol_recovery_due(identity.operator, now=datetime.now(timezone.utc))):
                try:
                    paper_recovery = run_paper_revalidation(root, identity, current, run, timeout=45)
                except subprocess.TimeoutExpired:
                    paper_recovery = {"source": "PAPER_RELEASE_REVALIDATION", "result": "NO_GO_PAPER_SCALPING",
                                      "reason": "PAPER_INTRADAY_REVALIDATION_TIMEOUT", "exit_code": 2}
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
            env = host_job_environment(root, identity, current, source)
            if unit == PREOPEN:
                command = ("/usr/bin/python3", "-B", str(source / "rc6_preopen.py"), "--publish-paper-gate")
                # The child already emits a typed JSON receipt and exit 2 for RED.
                # Keep its complete output private; journal only allowlisted metadata.
                with tempfile.TemporaryFile(dir=identity.state) as output:
                    result = run(command, cwd=root, env=env, check=False, stdout=output,
                                 stderr=subprocess.DEVNULL, timeout=600)
                    if result.returncode != 0:
                        receipt = read_preopen_red_receipt(output) if result.returncode == 2 else None
                        signature = ("PREOPEN_RED_CHECKS" if receipt is not None else
                                     "PREOPEN_CHILD_RECEIPT_UNVERIFIED" if result.returncode == 2 else
                                     "PREOPEN_CHILD_EXIT_" + str(result.returncode)
                                     if type(result.returncode) is int and 1 <= result.returncode <= 255 else
                                     "PREOPEN_CHILD_EXIT_UNVERIFIED")
                        preopen_failure = {"failure_signature": signature,
                                           **(receipt or {"preopen_red_checks": []}),
                                           "child_exit_code": result.returncode if type(result.returncode) is int else None,
                                           "exit_code": 2 if result.returncode == 2 else 1}
                    else:
                        preopen_failure = None
            else:
                # Only this unchanged publisher uses bare 'docker'. The shim
                # accepts its exact local formatting/status commands above.
                env["PATH"] = str(identity.state / "bin") + ":" + env["PATH"]
                command = ("/bin/sh", str(source / "scripts/porota_publish_introspection_github_rc6.sh"))
                result = run(command, cwd=root, env=env, check=False, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, timeout=600)
                require(result.returncode == 0, "SCHEDULER_HOST_JOB_FAILED")
                preopen_failure = None
            commands_result = [{"executor": "HOST_SSH_UID",
                                "result": "EXIT_ZERO" if preopen_failure is None else "PREOPEN_FAILED"}]
        observed_database = database_snapshot(root / DB_RELATIVE)
        if unit in IOL_JOBS:
            require(observed_database == current.get("database"), "IOL_RELEASE_DATABASE_IDENTITY_CHANGED")
        if unit == PREOPEN and preopen_failure is not None:
            return {"schema": "porota.rootless.job.v1", "unit": unit, "state": "BLOQUEADO",
                    "source_sha": current["source_sha"], "image_id": current["image_id"],
                    "real_orders_sent": 0, **preopen_failure}
        return {"schema": "porota.rootless.job.v1", "unit": unit, "state": "EXECUTED",
                "source_sha": current["source_sha"], "image_id": current["image_id"],
                "real_orders_sent": 0, "commands": commands_result,
                **({"paper_recovery": paper_recovery} if paper_recovery is not None else {}),
                "exit_code": 2 if any(row.get("exit_code") == 2 for row in commands_result) else 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unit", choices=sorted(ADAPTED_SERVICES | IOL_JOBS))
    parser.add_argument("--verify-iol-rest", action="store_true")
    parser.add_argument("--revalidate-paper-gate", action="store_true")
    parser.add_argument("--docker-bridge", nargs=argparse.REMAINDER)
    parser.add_argument("--publish-runtime-evidence", action="store_true")
    args = parser.parse_args(argv)
    try:
        identity = Identity.current()
        if args.publish_runtime_evidence:
            require(args.unit is None and not args.verify_iol_rest and not args.revalidate_paper_gate
                    and args.docker_bridge is None,
                    "SCHEDULER_UNIT_SCOPE")
            receipt = publish_runtime_evidence(identity)
            if receipt is not None:
                print(json.dumps(receipt, sort_keys=True))
            return 0
        if args.docker_bridge is not None:
            require(not args.revalidate_paper_gate, "SCHEDULER_UNIT_SCOPE")
            command = publisher_command(args.docker_bridge)
            with scheduler_lock(identity):
                manager, current, _ = verified_host(Path("/opt/porota-trading"), identity)
                execute_commands(manager, current, "observer", (command,))
            return 0
        require(args.unit is not None, "SCHEDULER_UNIT_REQUIRED")
        result = run_job(args.unit, identity=identity, verify_rest=args.verify_iol_rest,
                         revalidate_paper_gate=args.revalidate_paper_gate)
        print(json.dumps(result, sort_keys=True))
        return result["exit_code"]
    except Exception as exc:
        reason = str(exc) if isinstance(exc, Hold) else "UNCLASSIFIED_SCHEDULER_FAILURE"
        print(json.dumps({"schema": "porota.rootless.job.v1", "state": "SKIPPED" if reason == "DEPLOY_IN_PROGRESS" else "BLOQUEADO",
                          "failure_signature": reason, "unit": args.unit}))
        return 0 if reason == "DEPLOY_IN_PROGRESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
