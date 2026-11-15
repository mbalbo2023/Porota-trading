#!/usr/bin/env python3
"""One-time Porota rootless setup; inspection never changes the host.

Run through the authorized GitHub Action as the existing SSH account.  This
module never installs packages, changes another user's daemon, or prints
configuration values.  Routine promotion is in porota_rootless_host.py.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import pwd
import shutil
import stat
import subprocess

LEGACY_NAMES = (
    "porota_production_observer", "porota_production_dashboard", "porota_trading_bot",
)
SERVICE = "porota-rc6-docker.service"
DOCKER = "/usr/bin/docker"
SUDO = "/usr/bin/sudo"
SYSTEMCTL = "/usr/bin/systemctl"


def _call(command, *, run=subprocess.run, timeout=12):
    """Bound commands and discard arbitrary error text, including secrets."""
    account = pwd.getpwuid(os.getuid())
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C",
           "HOME": account.pw_dir, "USER": account.pw_name, "LOGNAME": account.pw_name,
           "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}",
           "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{os.getuid()}/bus"}
    try:
        value = run(tuple(command), check=False, text=True, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, timeout=timeout, env=env, cwd="/")
    except (OSError, subprocess.SubprocessError):
        return None
    if not isinstance(value.stdout, str) or len(value.stdout) > 262144:
        return None
    return value


def _metadata(path):
    try:
        meta = path.lstat()
    except FileNotFoundError:
        return {"present": False}
    except OSError:
        return {"present": None, "status": "NO_VERIFICADO"}
    return {"present": True, "uid": meta.st_uid, "gid": meta.st_gid,
            "mode": oct(stat.S_IMODE(meta.st_mode)), "symlink": stat.S_ISLNK(meta.st_mode),
            "socket": stat.S_ISSOCK(meta.st_mode), "readable": os.access(path, os.R_OK),
            "writable": os.access(path, os.W_OK)}


def _properties(value, allowed):
    if value is None or value.returncode != 0:
        return {"status": "NO_VERIFICADO"}
    return {key: item for line in value.stdout.splitlines()
            for key, separator, item in [line.partition("=")]
            if separator and key in allowed}


def inspect_host(root: Path, *, run=subprocess.run):
    """Observe fixed RC6 coordinates, not all host containers or applications."""
    uid = os.getuid()
    account = pwd.getpwuid(uid)
    home = Path(account.pw_dir)
    socket = Path(f"/run/user/{uid}/porota-rc6-docker.sock")
    sudo = _call((SUDO, "-n", "--", "/usr/bin/true"), run=run)
    legacy = []
    for name in LEGACY_NAMES:
        value = _call((SUDO, "-n", "--", DOCKER, "--host", "unix:///var/run/docker.sock",
                       "inspect", "--type", "container", name), run=run)
        row = {"name": name, "status": "NO_VERIFICADO"}
        if value is not None and value.returncode == 0:
            try:
                entries = json.loads(value.stdout)
                item = entries[0] if len(entries) == 1 else {}
                if item.get("Name") == "/" + name:
                    row.update({"status": item.get("State", {}).get("Status", "NO_VERIFICADO"),
                                "running": item.get("State", {}).get("Running"),
                                "cid": item.get("Id"), "image_id": item.get("Image"),
                                "data_mount_matches": any(
                                    m.get("Destination") == "/app/data" and
                                    m.get("Source") == str(root / "data")
                                    for m in item.get("Mounts", []))})
            except (ValueError, TypeError, KeyError, IndexError):
                pass
        legacy.append(row)
    own_info = {"status": "NO_VERIFICADO"}
    if socket.exists():
        info = _call((DOCKER, "--host", f"unix://{socket}", "info", "--format", "{{json .}}"), run=run)
        if info is not None and info.returncode == 0:
            try:
                raw = json.loads(info.stdout)
                own_info = {k: raw.get(k) for k in (
                    "ID", "SecurityOptions", "CgroupVersion", "CgroupDriver", "DockerRootDir")}
            except (ValueError, TypeError):
                pass
    user_service = _call((SYSTEMCTL, "--user", "show", SERVICE,
                          "--property=LoadState,ActiveState,SubState,FragmentPath"), run=run)
    linger = _call(("/usr/bin/loginctl", "show-user", str(uid), "--property=Linger"), run=run)
    controllers = []
    try:
        path = Path(f"/sys/fs/cgroup/user.slice/user-{uid}.slice/user@{uid}.service/cgroup.controllers")
        controllers = sorted(set(path.read_text(encoding="ascii").split()) & {"cpu", "memory", "pids"})
    except (OSError, UnicodeError):
        pass
    files = {relative: _metadata(root / relative) for relative in (
        "data", "data/paper_v17/observer_v17.db", "data/paper_v17/observer_v17.db-wal",
        "data/paper_v17/observer_v17.db-shm", ".env", ".secrets/ppi_production.json",
        "data/diagnosticos/dashboard_preview_v1633.env")}
    return {"schema": "porota.rootless.inspect.v1", "state": "NO_VERIFICADO",
            "operation": "inspect", "host_mutations": 0,
            "uid": uid, "gid": os.getgid(), "account": account.pw_name,
            "rootless_socket": str(socket), "socket_metadata": _metadata(socket),
            "conventional_user_socket": _metadata(Path(f"/run/user/{uid}/docker.sock")),
            "rootful_socket": _metadata(Path("/var/run/docker.sock")),
            "target_data_root": str(home / ".local/share/porota-rc6/docker"),
            "binaries": {name: shutil.which(name) is not None for name in (
                "docker", "dockerd-rootless.sh", "rootlesskit", "newuidmap", "newgidmap",
                "slirp4netns", "systemctl", "sudo")},
            "sudo_noninteractive_true": sudo is not None and sudo.returncode == 0,
            "user_service": _properties(user_service, {"LoadState", "ActiveState", "SubState", "FragmentPath"}),
            "linger": _properties(linger, {"Linger"}), "cgroup_controllers": controllers,
            "own_daemon": own_info, "legacy_rc6": legacy, "files": files}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("inspect",))
    parser.add_argument("--root", type=Path, default=Path("/opt/porota-trading"))
    args = parser.parse_args(argv)
    print(json.dumps(inspect_host(args.root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
