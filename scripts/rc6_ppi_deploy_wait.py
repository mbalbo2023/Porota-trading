"""Run Deploy V2 after the host's rootless lock becomes available."""
from pathlib import Path
import sys

from scripts import porota_actions_host as actions

WAIT_LOCK = """
import fcntl
import json
import os
from pathlib import Path
import pwd
import stat
import time

account = pwd.getpwuid(os.getuid())
path = Path(account.pw_dir) / ".local/state/porota-rc6/deploy.lock"
assert account.pw_uid > 0 and not any(p.is_symlink() for p in (path, *path.parents))
fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
try:
    meta = os.fstat(fd)
    assert stat.S_ISREG(meta.st_mode) and meta.st_uid == account.pw_uid
    assert meta.st_nlink == 1 and not meta.st_mode & 0o077
    inode = (os.major(meta.st_dev), os.minor(meta.st_dev), meta.st_ino)
    modes = set()
    deadline = time.monotonic() + 90
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if not modes:
                try:
                    for line in Path("/proc/locks").read_text().splitlines():
                        fields = line.split()
                        if len(fields) < 7 or fields[1] != "FLOCK":
                            continue
                        device = fields[5].split(":")
                        if (len(device) == 3 and
                                (int(device[0],16), int(device[1],16), int(device[2])) == inode):
                            modes.add({"READ":"SHARED", "WRITE":"EXCLUSIVE"}.get(fields[3], "UNKNOWN"))
                except (OSError, ValueError):
                    modes.add("UNKNOWN")
            if time.monotonic() >= deadline:
                raise SystemExit(79)
            time.sleep(1)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
            print(json.dumps({"state": "AVAILABLE", "observed_holder_modes": sorted(modes)}))
            break
finally:
    os.close(fd)
"""

original_ssh_run = actions.ssh_run

def preflight_owner_busy(process, arguments):
    if process.returncode != 1:
        return False
    try:
        receipt = actions.decode_receipt(process.stdout)
    except (TypeError, ValueError):
        return False
    return (receipt.get("schema") == "porota.rootless.deploy.v1"
            and receipt.get("phase") == "PREFLIGHT"
            and receipt.get("state") == "BLOQUEADO"
            and receipt.get("failure_signature") == "OWNER_BUSY"
            and receipt.get("roles") == {}
            and receipt.get("source_sha") == arguments[arguments.index("--source-sha") + 1]
            and receipt.get("source_tree") == arguments[arguments.index("--source-tree") + 1])

def guarded_ssh_run(ssh, command, *, data=None, timeout=180):
    if (len(command) < 6 or tuple(command[:2]) != ("python3", "-B")
            or command[3] != "deploy" or not command[2].endswith("/source/scripts/porota_rootless_host.py")):
        return original_ssh_run(ssh, command, data=data, timeout=timeout)
    actions.validate_stage(str(Path(command[2]).parents[2]))
    for _ in range(2):
        probe = original_ssh_run(ssh, ["python3", "-I", "-B", "-c", WAIT_LOCK], timeout=105)
        if probe.returncode != 0:
            print("DEPLOY_LOCK_AVAILABILITY=" + ("BUSY" if probe.returncode == 79 else "NO_VERIFICADO"),
                  flush=True)
            # Run the standard preflight once to obtain its authoritative reason.
            return original_ssh_run(ssh, command, data=data, timeout=timeout)
        lock = actions.decode_receipt(probe.stdout)
        modes = lock.get("observed_holder_modes")
        if (lock.get("state") != "AVAILABLE" or not isinstance(modes, list)
                or not set(modes) <= {"SHARED", "EXCLUSIVE", "UNKNOWN"}):
            raise ValueError("DEPLOY_LOCK_OBSERVATION_UNVERIFIED")
        print("DEPLOY_LOCK_AVAILABLE|holders=" + (",".join(modes) or "NONE_OBSERVED"), flush=True)
        receipt = original_ssh_run(ssh, command, data=data, timeout=timeout)
        if not preflight_owner_busy(receipt, command):
            return receipt
        print("DEPLOY_LOCK_REACQUIRED_BEFORE_CANONICAL_PREFLIGHT", flush=True)
    return receipt

if __name__ == "__main__":
    actions.ssh_run = guarded_ssh_run
    sys.argv.insert(1, "deploy")
    actions.main()
