"""Exercise the active RC6 timer inventory on a GitHub-hosted test runner."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

from scripts.porota_host_manifest_v2 import build_manifest
from scripts.porota_testing_scope import RELEASE_BLOCKED, require_release_tree


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "porota.iol.offhost.timer-inventory.v1"
CALENDAR_POLICY = "ops/policy/host-control-plane-reconciliation-v2.json"
DISPATCH = "SYSTEMD_TRANSIENT_PER_ACTIVE_TIMER"


def _directives(path, section):
    current = ""
    directives = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
        elif current == section and "=" in line and not line.startswith(("#", ";")):
            name, value = line.split("=", 1)
            directives.setdefault(name.strip(), []).append(value.strip())
    return directives


def timer_inventory(root, source_sha):
    policy = json.loads((root / CALENDAR_POLICY).read_bytes())
    manifest = build_manifest(root, policy, source_sha)
    if manifest["status"] != "GREEN":
        raise ValueError("TEST_TIMER_POLICY_PROVENANCE_INVALID")
    services = {}
    timers = []
    for unit in manifest["units"]:
        if not unit["install"] or unit["expected_masked"]:
            continue
        if unit["unit_kind"] == "timer" and unit["expected_active"] is True:
            timers.append(unit)
        if unit["unit_kind"] == "service" and unit["role"] == "ACTIVE_SERVICE":
            if unit["unit_name"] in services:
                raise ValueError("TEST_ACTIVE_SERVICE_DUPLICATE")
            services[unit["unit_name"]] = unit
    expected = {}
    for timer in timers:
        name = timer["unit_name"]
        directives = _directives(root / timer["source_path"], "Timer")
        target, = directives.get("Unit", [name.removesuffix(".timer") + ".service"])
        schedule = {key: values for key, values in directives.items() if key.startswith("On")}
        service = services.get(target)
        if (name in expected or not schedule or service is None
                or not _directives(root / service["source_path"], "Service").get("ExecStart")):
            raise ValueError("TEST_TIMER_SERVICE_BINDING_INVALID:" + name)
        expected[name] = {
            "timer": timer["source_path"], "timer_sha256": timer["source_sha256"],
            "service": service["source_path"], "service_sha256": service["source_sha256"],
            "schedule": schedule,
        }
    if not expected:
        raise ValueError("TEST_ACTIVE_TIMERS_MISSING")
    return expected


def _run(*command):
    return subprocess.run(command, capture_output=True, text=True, check=True, timeout=10).stdout


def check_timer_schedules(inventory):
    for timer in inventory.values():
        for key, values in timer["schedule"].items():
            if key == "OnCalendar":
                for value in values:
                    result = _run("systemd-analyze", "calendar", "--iterations=2",
                                  "--base-time=2026-11-26 00:00:00 UTC", value)
                    if "Next elapse:" not in result or "Next elapse: never" in result:
                        raise ValueError("TEST_TIMER_CALENDAR_NO_NEXT_TRIGGER")
            elif key in {"OnActiveSec", "OnBootSec", "OnStartupSec", "OnUnitActiveSec", "OnUnitInactiveSec"}:
                for value in values:
                    _run("systemd-analyze", "timespan", value)
            else:
                raise ValueError("TEST_TIMER_DIRECTIVE_UNSUPPORTED:" + key)


def _show(unit, *properties):
    raw = _run("systemctl", "show", unit, *(f"--property={prop}" for prop in properties))
    return dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)


def _dispatch(root, expected, temp, prefix, uid):
    launched = {}
    receipts = {}
    try:
        for index, (name, source) in enumerate(expected.items()):
            check = temp / name
            check.write_text("".join(
                f"{source[field + '_sha256']}  {root / source[field]}\n"
                for field in ("timer", "service")))
            unit = f"{prefix}-{index}"
            launched[name] = unit
            _run("sudo", "-n", "systemd-run", f"--unit={unit}", f"--uid={uid}",
                 "--on-active=2s", "--timer-property=AccuracySec=100ms",
                 "--property=Type=oneshot", "--property=RemainAfterExit=yes",
                 "--property=TimeoutStartSec=10s", "--property=NoNewPrivileges=yes",
                 "--property=PrivateNetwork=yes", "--property=ProtectSystem=strict",
                 "/usr/bin/sha256sum", "--check", "--status", str(check))
        deadline = time.monotonic() + 20
        while len(receipts) < len(launched) and time.monotonic() < deadline:
            for name, unit in launched.items():
                if name in receipts:
                    continue
                service = unit + ".service"
                worker = _show(service, "ActiveState", "Result", "ExecMainStatus", "User")
                if worker["ActiveState"] == "failed":
                    raise ValueError("TEST_TIMER_WORKER_FAILED:" + name)
                if worker["ActiveState"] != "active":
                    continue
                timer = _show(unit + ".timer", "Triggers", "LastTriggerUSecMonotonic")
                if (worker["Result"] != "success" or worker["ExecMainStatus"] != "0"
                        or worker["User"] != str(uid) or timer["Triggers"] != service
                        or timer["LastTriggerUSecMonotonic"] in {"", "0", "n/a"}):
                    raise ValueError("TEST_TIMER_TRIGGER_NOT_VERIFIED:" + name)
                receipts[name] = {**expected[name], "triggered": True, "worker_completed": True}
            if len(receipts) < len(launched):
                time.sleep(.2)
        if set(receipts) != set(expected):
            raise ValueError("TEST_TIMERS_INCOMPLETE:" + ",".join(sorted(set(expected) - set(receipts))))
    finally:
        if launched:
            command = [unit + suffix for unit in launched.values() for suffix in (".timer", ".service")]
            _run("sudo", "-n", "systemctl", "stop", *command)
    return receipts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run_id = os.getenv("GITHUB_RUN_ID", "")
    attempt = os.getenv("GITHUB_RUN_ATTEMPT", "")
    uid = os.getuid()
    if (os.getenv("RUNNER_ENVIRONMENT") != "github-hosted" or os.getenv("GITHUB_ACTIONS") != "true"
            or os.getenv("GITHUB_REF") != "refs/heads/qa-rc6-iol-migration-20261126"
            or not run_id.isdecimal() or not attempt.isdecimal() or uid == 0
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", args.image_id)
            or _run("git", "-C", str(ROOT), "rev-parse", "HEAD", "HEAD^{tree}").splitlines()
            != [args.source_sha, args.source_tree]):
        raise ValueError("TEST_TIMER_OFFHOST_CANDIDATE_REQUIRED")
    try:
        require_release_tree(ROOT, "HEAD")
    except ValueError as error:
        if str(error) != RELEASE_BLOCKED:
            raise
    else:
        raise ValueError("TEST_TIMER_RELEASE_GUARD_MISSING")
    timers = timer_inventory(ROOT, args.source_sha)
    check_timer_schedules(timers)
    with tempfile.TemporaryDirectory(prefix="rc6-iol-timers-", dir=os.environ["RUNNER_TEMP"]) as path:
        receipts = _dispatch(ROOT, timers, Path(path), f"rc6-iol-clock-{run_id}-{attempt}", uid)
    result = {"schema": SCHEMA, "source_sha": args.source_sha, "source_tree": args.source_tree,
              "image_id": args.image_id, "dispatch": DISPATCH, "worker": "READ_ONLY_SOURCE_HASH_CHECK",
              "production_service_bodies": "NOT_EXECUTED", "timers": receipts}
    args.output.write_text(json.dumps(result, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
