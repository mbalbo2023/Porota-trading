#!/usr/bin/env python3
"""Deploy V2 installation of the two IOL REST service drop-ins."""
from __future__ import annotations

import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys
import tempfile


UNIT_ROOT = Path("/etc/systemd/system")
PRODUCT_ROOT = Path("/opt/porota-trading")
SYSTEMCTL = "/usr/bin/systemctl"
OVERRIDE = "95-porota-iol-rest.conf"
IOL_HEALTH = "porota-iol-oauth-health-rc6.service"
LEGACY = {
    "porota-iol-shadow-collector-rc6.service": ("/scripts/rc6_source_cycle_dispatch.py", frozenset()),
    IOL_HEALTH: ("/scripts/rc6_iol_oauth_health.py", frozenset({"90-porota-iol-release.conf"})),
}
SERVICE_KEYS = "ActiveState,LoadState,FragmentPath,DropInPaths,NeedDaemonReload,ExecStart,User,Group,Conditions,WorkingDirectory"
TIMER_KEYS = "LoadState,ActiveState,UnitFileState,Triggers,TimersCalendar,TimersMonotonic,AccuracyUSec,Persistent"


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def checked_path(path, owner, *, directory=False):
    require(not any(item.is_symlink() for item in (path, *path.parents)), "IOL_UNIT_PATH_ALIAS")
    info = path.lstat()
    correct_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    require(info.st_uid == owner and not info.st_mode & 0o022 and correct_type, "IOL_UNIT_PATH_CUSTODY")


def systemctl(*args):
    result = subprocess.run((SYSTEMCTL, *args), capture_output=True, text=True, timeout=30, check=False)
    require(result.returncode == 0, "IOL_SYSTEMD_COMMAND_FAILED")
    return result.stdout


def properties(unit, keys):
    return dict(line.split("=", 1) for line in systemctl("show", "--no-pager", "-p", keys, unit).splitlines() if "=" in line)


def launcher_command(account, unit):
    return (f"/usr/bin/env -i HOME={account.pw_dir} USER={account.pw_name} LOGNAME={account.pw_name} "
            "PATH=/usr/bin:/bin:/usr/sbin:/sbin POROTA_RUNTIME_BACKEND=ROOTLESS PYTHONDONTWRITEBYTECODE=1 "
            f"/usr/bin/python3 -B {account.pw_dir}/.local/state/porota-rc6/scheduler-dispatch.py --unit {unit}")


def definition(account, unit, *, pin_directory=True):
    directory = f"WorkingDirectory={PRODUCT_ROOT}\n" if pin_directory and unit == IOL_HEALTH else ""
    return ("[Unit]\nDescription=Porota RC6 IOL REST OBSERVE_ONLY\nConditionPathExists=\n\n"
            f"[Service]\nUser={account.pw_name}\nGroup={account.pw_gid}\n{directory}ExecStart=\n"
            f"ExecStart={launcher_command(account, unit)}\n").encode()


def effective(service, account, unit, path):
    match = re.search(r"argv\[\]=([^;]+);", service.get("ExecStart", ""))
    return (service.get("User") == account.pw_name and service.get("Group") == str(account.pw_gid)
            and str(path) in service.get("DropInPaths", "").split() and service.get("NeedDaemonReload") == "no"
            and "iol_mcp_oauth_bootstrap.json" not in service.get("Conditions", "")
            and match is not None and match[1].strip() == launcher_command(account, unit))


def working_directory_effective(service, unit):
    return unit != IOL_HEALTH or service.get("WorkingDirectory") == str(PRODUCT_ROOT)


def publish(path, content):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".porota-iol-rest-", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def reconcile(account, *, install):
    require(account.pw_uid > 0 and account.pw_gid > 0 and re.fullmatch(r"[a-z_][a-z0-9_-]*", account.pw_name)
            and re.fullmatch(r"/[a-zA-Z0-9_/.-]+", account.pw_dir) and ".." not in Path(account.pw_dir).parts,
            "IOL_UNIT_ACCOUNT_INVALID")
    state = Path(account.pw_dir) / ".local/state/porota-rc6"
    checked_path(state, account.pw_uid, directory=True)
    checked_path(state / "scheduler-dispatch.py", account.pw_uid)
    checked_path(UNIT_ROOT, 0, directory=True)
    checked_path(PRODUCT_ROOT, account.pw_uid, directory=True)
    plans, timers = {}, {}
    for unit, (legacy_command, legacy_overrides) in LEGACY.items():
        timer = unit.removesuffix(".service") + ".timer"
        timer_state = properties(timer, TIMER_KEYS)
        require(timer_state.get("LoadState") == "loaded" and timer_state.get("Triggers") == unit,
                "IOL_TIMER_IDENTITY_UNVERIFIED")
        timers[timer] = timer_state
        service = properties(unit, SERVICE_KEYS)
        fragment = UNIT_ROOT / unit
        require(service.get("LoadState") == "loaded" and service.get("FragmentPath") == str(fragment),
                "IOL_UNIT_IDENTITY_UNVERIFIED")
        checked_path(fragment, 0)
        directory = UNIT_ROOT / (unit + ".d")
        path = directory / OVERRIDE
        if directory.exists() or directory.is_symlink():
            checked_path(directory, 0, directory=True)
        for entry in service.get("DropInPaths", "").split():
            dropin = Path(entry)
            require(dropin.parent == directory and dropin.name in legacy_overrides | {OVERRIDE}, "IOL_UNIT_DROPIN_UNREVIEWED")
            checked_path(dropin, 0)
        content = definition(account, unit)
        present = path.exists() or path.is_symlink()
        installed_content = None
        if present:
            checked_path(path, 0)
            installed_content = path.read_bytes()
            # Only replace the exact legacy drop-in published by the installer.
            require(installed_content in {content, definition(account, unit, pin_directory=False)},
                    "IOL_UNIT_OVERRIDE_DRIFT")
        already_effective = effective(service, account, unit, path)
        if not already_effective:
            require(service.get("User") in ("", "root") and legacy_command in service.get("ExecStart", ""),
                    "IOL_LEGACY_UNIT_UNREVIEWED")
        change = not already_effective or installed_content != content or not working_directory_effective(service, unit)
        if change:
            plans[unit] = (path, content)
    if install and plans:
        systemctl("stop", *plans)
        for path, content in plans.values():
            path.parent.mkdir(mode=0o755, exist_ok=True)
            checked_path(path.parent, 0, directory=True)
            publish(path, content)
        systemctl("daemon-reload")
    if install:
        for unit in LEGACY:
            service = properties(unit, SERVICE_KEYS)
            path = UNIT_ROOT / (unit + ".d") / OVERRIDE
            require(effective(service, account, unit, path) and working_directory_effective(service, unit),
                    "IOL_REST_UNIT_NOT_EFFECTIVE")
        require(all(properties(timer, TIMER_KEYS) == state for timer, state in timers.items()), "IOL_TIMER_STATE_CHANGED")
    return {"state": "EFFECTIVE" if install else "PREFLIGHT_OK", "source": "IOL_REST",
            "services": {unit: {"configured": install, "uid": account.pw_uid} for unit in LEGACY},
            "timers": {unit: state.get("ActiveState") for unit, state in timers.items()}}


def main():
    try:
        require(os.geteuid() == 0 and len(sys.argv) == 2 and sys.argv[1] in {"preflight", "install"},
                "IOL_UNIT_MODE_DENIED")
        result = reconcile(pwd.getpwuid(int(os.environ["SUDO_UID"])), install=sys.argv[1] == "install")
    except (ValueError, OSError, subprocess.SubprocessError, KeyError) as exc:
        reason = str(exc)
        print(json.dumps({"state": "BLOQUEADO", "reason": reason if re.fullmatch(r"IOL_[A-Z_]+", reason)
                          else "IOL_UNIT_INSPECTION_FAILED"}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
