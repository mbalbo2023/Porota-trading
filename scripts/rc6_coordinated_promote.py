"""Drain one IOL timer while retaining the exact canonical Deploy V2 release."""
from contextlib import contextmanager, redirect_stdout
import io
import json
import os
from pathlib import Path
import pwd
import re
import signal
import stat
import subprocess
import sys


TIMER = "porota-iol-shadow-collector-rc6.timer"
SERVICE = "porota-iol-shadow-collector-rc6.service"
SYSTEMCTL = "/usr/bin/systemctl"
SUDO = "/usr/bin/sudo"
FIELDS = "LoadState,ActiveState,UnitFileState,Triggers,FragmentPath,DropInPaths"
SERVICE_LINKS = "PartOf,BindsTo,Requires"


def iol_holds_deploy_lock(path):
    meta = path.lstat()
    if not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.getuid() or meta.st_nlink != 1:
        return False
    expected = (os.major(meta.st_dev), os.minor(meta.st_dev), meta.st_ino)
    shared_iol = False
    with Path("/proc/locks").open("r", encoding="ascii") as source:
        for line in source:
            pieces = line.split()
            if len(pieces) < 8 or pieces[1] != "FLOCK":
                continue
            entity = re.fullmatch(r"([0-9a-fA-F]+):([0-9a-fA-F]+):(\d+)", pieces[5])
            if entity is None or (int(entity[1], 16), int(entity[2], 16), int(entity[3])) != expected:
                continue
            if pieces[3] != "READ":
                return False
            try:
                pid = int(pieces[4])
                if pid <= 0:
                    continue
                with (Path("/proc") / str(pid) / "cgroup").open("r", encoding="ascii") as f:
                    group = f.read(8192)
                shared_iol = shared_iol or SERVICE in {part for line in group.splitlines()
                                                      for part in line.split("/")}
            except (OSError, UnicodeError, ValueError):
                continue
    return shared_iol


def unit_properties(run):
    p = run((SYSTEMCTL, "show", "--no-pager", "-p", FIELDS, TIMER), timeout=8)
    if p.returncode != 0 or len(p.stdout) > 8192:
        return None
    return dict(line.split("=", 1) for line in p.stdout.splitlines() if "=" in line)


def command(*, run, operation, list_only=False):
    permission = ("-l",) if list_only else ()
    return run((SUDO, "-n", *permission, "--", SYSTEMCTL, operation, TIMER), timeout=16).returncode == 0


def service_can_drain(run):
    p = run((SYSTEMCTL, "show", "--no-pager", "-p", SERVICE_LINKS, SERVICE), timeout=8)
    if p.returncode != 0 or len(p.stdout) > 8192:
        return False
    links = dict(line.split("=", 1) for line in p.stdout.splitlines() if "=" in line)
    return set(links) == set(SERVICE_LINKS.split(",")) and all(TIMER not in v.split() for v in links.values())


@contextmanager
def promote_lock(original, error, path, *, wait_seconds, expected_path, trace, run, holders):
    """Pause new IOL triggers only while an observed IOL reader holds the lock."""
    if path != expected_path or wait_seconds <= 0 or not holders(path):
        trace["state"] = "CANONICAL_LOCK"
        with original(path, wait_seconds=wait_seconds):
            yield
        return
    initial = unit_properties(run)
    if (not initial or initial.get("LoadState") != "loaded" or initial.get("Triggers") != SERVICE
            or initial.get("ActiveState") != "active"):
        trace["state"] = "IOL_TIMER_NOT_ACTIVE_OR_UNVERIFIED"
        with original(path, wait_seconds=wait_seconds):
            yield
        return
    if not all(command(run=run, operation=op, list_only=True) for op in ("start", "stop")):
        raise error("IOL_DEPLOY_TIMER_PERMISSION")
    if not service_can_drain(run):
        raise error("IOL_DEPLOY_TIMER_HAS_SERVICE_DEPENDENCY")
    trace.update(state="IOL_TIMER_DRAIN", originally_active=True, restored=False)
    pending_restore = True

    def restore():
        if not command(run=run, operation="start"):
            raise error("IOL_DEPLOY_TIMER_RESTORE_FAILED")
        if unit_properties(run) != initial:
            raise error("IOL_DEPLOY_TIMER_RESTORE_UNVERIFIED")
        trace["restored"] = True

    try:
        if not command(run=run, operation="stop"):
            raise error("IOL_DEPLOY_TIMER_PAUSE_FAILED")
        paused = unit_properties(run)
        if (not paused or paused.get("ActiveState") != "inactive"
                or {k:v for k,v in paused.items() if k != "ActiveState"}
                != {k:v for k,v in initial.items() if k != "ActiveState"}):
            raise error("IOL_DEPLOY_TIMER_PAUSE_UNVERIFIED")
        trace["schedule_paused"] = True
        # The active service can finish naturally. The same FLOCK owned by
        # the canonical deployment admits the writer only after it drained.
        with original(path, wait_seconds=wait_seconds):
            trace["exclusive_lock_obtained"] = True
            restore()
            pending_restore = False
            trace["state"] = "IOL_TIMER_RESTORED_UNDER_DEPLOY_LOCK"
            yield
    finally:
        if pending_restore:
            restore()


def on_host(arguments):
    account = pwd.getpwuid(os.getuid())
    stage_root = Path(account.pw_dir) / ".local/state/porota-rc6"
    target = Path(arguments[0])
    if (account.pw_uid <= 0 or os.geteuid() != account.pw_uid or arguments[1:4] !=
            ["deploy", "--root", "/opt/porota-trading"] or target.name != "porota_rootless_host.py"
            or target.parent.name != "scripts" or target.parent.parent.name != "source"
            or target.parents[3] != stage_root or not re.fullmatch(r"stage\.[A-Za-z0-9_-]+", target.parents[2].name)
            or any(p.is_symlink() for p in (target, *target.parents))):
        raise ValueError("DEPLOY_COORDINATION_SOURCE_UNVERIFIED")
    sys.path.insert(0, str(target.parent.parent))
    from scripts import porota_rootless_host as host
    if Path(host.__file__).resolve() != target.resolve():
        raise ValueError("DEPLOY_COORDINATION_SOURCE_UNVERIFIED")
    original, trace = host.user_lock, {}

    def fixed_command(argv, *, timeout):
        try:
            return subprocess.run(argv, check=False, capture_output=True, text=True, timeout=timeout,
                                  env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C"})
        except (OSError, subprocess.TimeoutExpired):
            raise host.Hold("IOL_DEPLOY_TIMER_COMMAND_UNAVAILABLE") from None

    def holds(path):
        try:
            return iol_holds_deploy_lock(path)
        except (OSError, ValueError, UnicodeError):
            return False

    def interrupted(_signum, _frame):
        raise host.Hold("IOL_DEPLOY_COORDINATION_INTERRUPTED")

    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, interrupted)

    def coordinate(path, *, wait_seconds=0):
        return promote_lock(original, host.Hold, path, wait_seconds=wait_seconds,
                            expected_path=stage_root / "deploy.lock", trace=trace, run=fixed_command,
                            holders=holds)
    host.user_lock = coordinate
    output = io.StringIO()
    with redirect_stdout(output):
        result = host.main(arguments[1:])
    receipt = json.loads(output.getvalue())
    receipt["iol_deploy_coordination"] = trace
    print(json.dumps(receipt, separators=(",", ":"), sort_keys=True))
    return result


def from_actions():
    from scripts import porota_actions_host as actions
    original = actions.ssh_run
    program = Path(__file__).read_bytes()

    def coordinated(ssh, args, *, data=None, timeout=180):
        if (len(args) > 5 and args[:2] == ["python3", "-B"]
                and str(args[2]).endswith("/source/scripts/porota_rootless_host.py")
                and args[3:6] == ["deploy", "--root", "/opt/porota-trading"]):
            actions.validate_stage(str(Path(args[2]).parents[2]))
            args = ["/usr/bin/python3", "-I", "-B", "-", "--host", *args[2:]]
            return original(ssh, args, data=program, timeout=timeout)
        return original(ssh, args, data=data, timeout=timeout)

    actions.ssh_run = coordinated
    sys.argv.insert(1, "deploy")
    actions.main()


if __name__ == "__main__":
    if sys.argv[1:2] == ["--host"]:
        raise SystemExit(on_host(sys.argv[2:]))
    from_actions()
