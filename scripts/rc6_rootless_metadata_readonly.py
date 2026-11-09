"""Bounded READ_ONLY metadata from Porota's private rootless Docker socket.

Never autodetect a Docker context, query the shared rootful daemon, enumerate
foreign objects, mutate a host resource or claim runtime certification.
This file is streamed over the existing, host-pinned Actions SSH connection.
"""

import json
import os
from pathlib import Path
import pwd
import re
import select
import stat
import subprocess
import sys
import time


SCHEMA = "rc6.r1.rootless-private-readonly.v1"
PRIVATE_ACCOUNT = "porota-rc6"
MAX_ENGINE_INFO = 16384
CURRENT_LOGIN_SCHEMA = "rc6.r1.current-login-capability-readonly.v1"
CURRENT_LOGIN_EXPECTED_UID = 1000  # operator-observed A1013, not an OS policy
MAX_SUBID_BYTES = 131072


class ProbeError(Exception):
    """Fixed, public-safe failure category, not a host exception string."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _lstat(path, reason):
    try:
        return os.lstat(str(path))
    except OSError:
        raise ProbeError(reason) from None


def _private_account():
    try:
        account = pwd.getpwnam(PRIVATE_ACCOUNT)
    except KeyError:
        raise ProbeError("DEDICATED_ACCOUNT_UNVERIFIED") from None
    home = Path(account.pw_dir)
    if (account.pw_name != PRIVATE_ACCOUNT or account.pw_uid < 1000
            or account.pw_gid < 1000 or not home.is_absolute()
            or home.name != PRIVATE_ACCOUNT or ".." in home.parts):
        raise ProbeError("DEDICATED_ACCOUNT_UNVERIFIED")
    if len([user for user in pwd.getpwall() if user.pw_uid == account.pw_uid]) != 1:
        raise ProbeError("DEDICATED_ACCOUNT_UNVERIFIED")
    if os.geteuid() != account.pw_uid:
        raise ProbeError("REMOTE_UID_NOT_PRIVATE")
    info = _lstat(home, "PRIVATE_HOME_UNVERIFIED")
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != account.pw_uid
            or (stat.S_IMODE(info.st_mode) & 0o077) != 0):
        raise ProbeError("PRIVATE_HOME_UNVERIFIED")
    return account, home


def _private_socket(account):
    runtime = Path("/run/user") / str(account.pw_uid)
    info = _lstat(runtime, "PRIVATE_RUNTIME_UNVERIFIED")
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != account.pw_uid
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise ProbeError("PRIVATE_RUNTIME_UNVERIFIED")
    socket = runtime / "docker.sock"
    info = _lstat(socket, "PRIVATE_SOCKET_UNVERIFIED")
    if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != account.pw_uid
            or (stat.S_IMODE(info.st_mode) & 0o007) != 0):
        raise ProbeError("PRIVATE_SOCKET_UNVERIFIED")
    return socket


def _run_bounded_command(argv, *, env, timeout, max_bytes):
    """Stop before reading more than max_bytes; never buffer child stderr."""
    proc = None
    try:
        proc = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
        fd = proc.stdout.fileno()
        os.set_blocking(fd, False)
        deadline = time.monotonic() + timeout
        output = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
                raise ProbeError("PRIVATE_ENGINE_UNVERIFIED")
            try:
                chunk = os.read(fd, min(4096, max_bytes + 1 - len(output)))
            except BlockingIOError:
                continue
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > max_bytes:
                raise ProbeError("PRIVATE_ENGINE_UNVERIFIED")
        proc.wait(timeout=max(0.001, deadline - time.monotonic()))
        if proc.returncode != 0:
            raise ProbeError("PRIVATE_ENGINE_UNVERIFIED")
        return output.decode("utf-8", "strict")
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
        raise ProbeError("PRIVATE_ENGINE_UNVERIFIED") from None
    finally:
        if proc is not None:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            if proc.stdout is not None:
                proc.stdout.close()


def _engine_info(socket, home):
    # Explicitly pinned private socket: no implicit DOCKER_HOST, context, TLS
    # selector, default /var/run/docker.sock or global inventory commands.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("DOCKER_")}
    env["HOME"] = str(home)
    env["XDG_RUNTIME_DIR"] = str(socket.parent)
    env["PATH"] = "/usr/bin:/bin"
    output = _run_bounded_command(
        ["docker", "--host", f"unix://{socket}",
         "info", "--format", "{{json .}}"],
        env=env, timeout=8, max_bytes=MAX_ENGINE_INFO,
    )
    try:
        data = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        raise ProbeError("PRIVATE_ENGINE_UNVERIFIED") from None
    if not isinstance(data, dict):
        raise ProbeError("PRIVATE_ENGINE_UNVERIFIED")
    security = data.get("SecurityOptions")
    if not isinstance(security, list) or "name=rootless" not in security:
        raise ProbeError("ENGINE_NOT_ROOTLESS")
    return data


def _private_data_root(account, home, data):
    text = data.get("DockerRootDir")
    if (not isinstance(text, str) or len(text) > 256
            or not text.startswith("/") or ".." in Path(text).parts):
        raise ProbeError("DATA_ROOT_OUTSIDE_PRIVATE_HOME")
    root = Path(text)
    try:
        parts = root.relative_to(home).parts
    except ValueError:
        raise ProbeError("DATA_ROOT_OUTSIDE_PRIVATE_HOME") from None
    if not parts:
        raise ProbeError("DATA_ROOT_OUTSIDE_PRIVATE_HOME")
    current = home
    for index, part in enumerate(parts):
        current = current / part
        info = _lstat(current, "PRIVATE_DATA_ROOT_UNVERIFIED")
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != account.pw_uid
                or (stat.S_IMODE(info.st_mode) & 0o022)):
            raise ProbeError("PRIVATE_DATA_ROOT_UNVERIFIED")
        if index == len(parts) - 1 and stat.S_IMODE(info.st_mode) & 0o077:
            raise ProbeError("PRIVATE_DATA_ROOT_UNVERIFIED")


def collect():
    account, home = _private_account()
    socket = _private_socket(account)
    info = _engine_info(socket, home)
    _private_data_root(account, home, info)
    return {
        "schema": SCHEMA,
        "state": "READ_ONLY_OBSERVED_NOT_CERTIFIED",
        "read_only": True,
        "mutations": 0,
        "provider_gets": 0,
        "porota_private_account": True,
        "porota_private_socket": True,
        "porota_rootless_engine": True,
        "porota_private_data_root": True,
        "foreign_docker_inspected": False,
        "rootless_certified": False,
    }


def _current_login_account():
    """Use the already-proven operator UID, never infer sudo/root authority."""
    uid = os.geteuid()
    if uid != CURRENT_LOGIN_EXPECTED_UID or uid < 1000:
        raise ProbeError("CURRENT_LOGIN_IDENTITY_UNVERIFIED")
    try:
        account = pwd.getpwuid(uid)
        matches = [entry for entry in pwd.getpwall() if entry.pw_uid == uid]
    except (KeyError, OSError):
        raise ProbeError("CURRENT_LOGIN_IDENTITY_UNVERIFIED") from None
    home = Path(account.pw_dir)
    if (account.pw_uid != uid or account.pw_gid < 1000 or len(matches) != 1
            or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", account.pw_name)
            or not home.is_absolute() or ".." in home.parts):
        raise ProbeError("CURRENT_LOGIN_IDENTITY_UNVERIFIED")
    info = _lstat(home, "CURRENT_LOGIN_HOME_UNVERIFIED")
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != uid
            or stat.S_IMODE(info.st_mode) & 0o022):
        raise ProbeError("CURRENT_LOGIN_HOME_UNVERIFIED")
    return account


def _login_subid_text(path):
    """Bounded root-owned metadata read; never print existing account ranges."""
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                or stat.S_IMODE(info.st_mode) & 0o022 or info.st_size > MAX_SUBID_BYTES):
            raise ValueError("UNTRUSTED_SUBID_METADATA")
        return os.read(fd, MAX_SUBID_BYTES + 1).decode("utf-8", "strict")
    finally:
        os.close(fd)


def _login_subids_ready(account):
    """Require a dedicated >=65536-ID block for this login in BOTH maps."""
    try:
        for path in ("/etc/subuid", "/etc/subgid"):
            text = _login_subid_text(path)
            if len(text.encode("utf-8")) > MAX_SUBID_BYTES:
                return False
            records = []
            for line in text.splitlines():
                if not line.strip():
                    continue
                parts = line.split(":")
                if (len(records) >= 1024 or len(parts) != 3
                        or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", parts[0])
                        or not re.fullmatch(r"[0-9]{1,10}", parts[1])
                        or not re.fullmatch(r"[0-9]{1,10}", parts[2])):
                    return False
                start, count = int(parts[1]), int(parts[2])
                if count < 1 or start + count > 2147483648:
                    return False
                records.append((parts[0], start, start + count))
            owner_keys = {account.pw_name, str(account.pw_uid)}
            own = [(start, end) for name, start, end in records if name in owner_keys]
            other = [(start, end) for name, start, end in records if name not in owner_keys]
            if (not own or any(start < 65536 for start, _ in own)
                    or any(not (end <= left or start >= right)
                           for start, end in own for left, right in other)
                    or not any(end - start >= 65536 for start, end in own)):
                return False
        return True
    except (OSError, UnicodeError, ValueError):
        return False


def _login_rootless_toolchain():
    """Read filesystem metadata only: no Docker daemon interaction."""
    paths = ("/usr/bin/docker", "/usr/bin/rootlesskit",
             "/usr/bin/dockerd-rootless-setuptool.sh", "/usr/bin/newuidmap",
             "/usr/bin/newgidmap", "/usr/bin/loginctl", "/usr/bin/systemctl")
    try:
        for path in paths:
            info = os.stat(path)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                    or not info.st_mode & 0o111 or info.st_mode & 0o022):
                return False
            if path.endswith(("newuidmap", "newgidmap")) and not info.st_mode & stat.S_ISUID:
                return False
        return True
    except OSError:
        return False


def _trusted_root_metadata(path, kind, *, executable=False):
    """Do not follow untrusted leaf symlinks or accept writable OS metadata."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return (kind(info.st_mode) and info.st_uid == 0
            and not stat.S_IMODE(info.st_mode) & 0o022
            # Launcher is a shell script: UID1000 needs world read+execute,
            # not merely owner-root execute bits.
            and (not executable or (stat.S_IMODE(info.st_mode) & 0o005) == 0o005))


def _login_rootless_launcher_present():
    """Presence only: a trusted launcher does not prove a running daemon."""
    return (_trusted_root_metadata('/usr', stat.S_ISDIR)
            and _trusted_root_metadata('/usr/bin', stat.S_ISDIR)
            and _trusted_root_metadata('/usr/bin/dockerd-rootless.sh',
                                       stat.S_ISREG, executable=True))


def _login_linger_record_present(account):
    """Trust only the root-owned systemd linger marker; run no commands."""
    parents = ('/var', '/var/lib', '/var/lib/systemd', '/var/lib/systemd/linger')
    return (all(_trusted_root_metadata(path, stat.S_ISDIR) for path in parents)
            and _trusted_root_metadata(parents[-1] + '/' + account.pw_name,
                                       stat.S_ISREG))


def _login_userns_ready():
    try:
        def integer(path):
            with open(path, "rb") as stream:
                raw = stream.read(33)
            return int(raw) if re.fullmatch(rb"[0-9]{1,20}\s*", raw) else 0
        return (integer("/proc/sys/kernel/unprivileged_userns_clone") == 1
                and integer("/proc/sys/user/max_user_namespaces") > 0
                and Path("/sys/fs/cgroup/cgroup.controllers").is_file())
    except (OSError, ValueError):
        return False


def _login_runtime_inventory(account):
    """Never contact any socket: same-UID ownership is not Porota provenance."""
    runtime = Path("/run/user") / str(account.pw_uid)
    info = _lstat(runtime, "CURRENT_LOGIN_RUNTIME_UNVERIFIED")
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != account.pw_uid
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise ProbeError("CURRENT_LOGIN_RUNTIME_UNVERIFIED")
    bus = runtime / "bus"
    try:
        info = os.lstat(str(bus))
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != account.pw_uid:
            raise ProbeError("CURRENT_LOGIN_BUS_UNVERIFIED")
        bus_present = True
    except FileNotFoundError:
        bus_present = False

    def absent(path):
        try:
            # DO NOT connect: a socket could belong to an unrelated project.
            os.lstat(str(path))
            return False
        except FileNotFoundError:
            return True
        except OSError:
            raise ProbeError("CURRENT_LOGIN_SOCKET_UNVERIFIED") from None

    return bus_present, absent(runtime / "docker.sock"), absent(runtime / "porota-rc6")


def collect_current_login_readonly():
    account = _current_login_account()
    bus, default_absent, project_absent = _login_runtime_inventory(account)
    rootless_tools = _login_rootless_toolchain()
    rootless_launcher = _login_rootless_launcher_present()
    linger_record = _login_linger_record_present(account)
    subids = _login_subids_ready(account)
    userns = _login_userns_ready()
    ready = all((bus, default_absent, project_absent, rootless_tools,
                 rootless_launcher, linger_record, subids, userns))
    return {
        "schema": CURRENT_LOGIN_SCHEMA,
        "state": "NO_VERIFICADO",
        "read_only": True, "mutations": 0, "provider_gets": 0,
        "docker_queries": 0, "socket_connections": 0,
        "sudo_executed": False, "foreign_docker_inspected": False,
        "principal_nonroot_unique": True,
        "runtime_directory_private": True,
        "user_manager_socket_present": bus,
        "rootless_tools_present": rootless_tools,
        "rootless_launcher_present": rootless_launcher,
        "user_linger_record_present": linger_record,
        "subuids_assigned": subids, "userns_enabled": userns,
        "default_socket_absent": default_absent,
        "project_socket_namespace_absent": project_absent,
        "ready_for_private_rootless_review": ready,
        "rootless_certified": False,
    }


def main_current_login():
    try:
        receipt = collect_current_login_readonly()
    except ProbeError as exc:
        receipt = {
            "schema": CURRENT_LOGIN_SCHEMA, "state": "NO_VERIFICADO",
            "read_only": True, "mutations": 0, "provider_gets": 0,
            "docker_queries": 0, "socket_connections": 0,
            "sudo_executed": False, "rootless_certified": False,
            "reason": exc.reason,
        }
    except Exception:
        receipt = {
            "schema": CURRENT_LOGIN_SCHEMA, "state": "NO_VERIFICADO",
            "read_only": True, "mutations": 0, "provider_gets": 0,
            "docker_queries": 0, "socket_connections": 0,
            "sudo_executed": False, "rootless_certified": False,
            "reason": "CURRENT_LOGIN_PROBE_UNVERIFIED",
        }
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


def main():
    try:
        receipt = collect()
    except ProbeError as exc:
        receipt = {
            "schema": SCHEMA,
            "state": "NO_VERIFICADO",
            "read_only": True,
            "mutations": 0,
            "provider_gets": 0,
            "rootless_certified": False,
            "reason": exc.reason,
        }
    except Exception:
        receipt = {
            "schema": SCHEMA,
            "state": "NO_VERIFICADO",
            "read_only": True,
            "mutations": 0,
            "provider_gets": 0,
            "rootless_certified": False,
            "reason": "PRIVATE_PROBE_UNVERIFIED",
        }
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--current-login"]:
        raise SystemExit(main_current_login())
    if len(sys.argv) != 1:
        raise SystemExit(79)
    raise SystemExit(main())
