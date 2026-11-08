"""Bounded READ_ONLY metadata from Porota's private rootless Docker socket.

Never autodetect a Docker context, query the shared rootful daemon, enumerate
foreign objects, mutate a host resource or claim runtime certification.
This file is streamed over the existing, host-pinned Actions SSH connection.
"""

import json
import os
from pathlib import Path
import pwd
import select
import stat
import subprocess
import time


SCHEMA = "rc6.r1.rootless-private-readonly.v1"
PRIVATE_ACCOUNT = "porota-rc6"
MAX_ENGINE_INFO = 16384


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
    raise SystemExit(main())
