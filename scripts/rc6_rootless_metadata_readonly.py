"""Bounded READ_ONLY metadata from Porota's private rootless Docker socket.

Never autodetect a Docker context, query the shared rootful daemon, enumerate
foreign objects, mutate a host resource or claim runtime certification.
This file is streamed over the existing, host-pinned Actions SSH connection.
"""

import errno
import json
import grp
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
DEDICATED_PREFLIGHT_SCHEMA = "rc6.r1.dedicated-account-preflight-readonly.v1"
DEDICATED_LOGIN = "porotaadmin"
MAX_PROC_NET_BYTES = 65536


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
    """Bounded root-owned file read; never print existing account data."""
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                or stat.S_IMODE(info.st_mode) & 0o022 or info.st_size > MAX_SUBID_BYTES):
            raise ValueError("UNTRUSTED_SUBID_METADATA")
        raw = bytearray()
        while True:
            chunk = os.read(fd, min(4096, MAX_SUBID_BYTES + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > MAX_SUBID_BYTES:
                raise ValueError("UNTRUSTED_SUBID_BOUND")
        return raw.decode("utf-8", "strict")
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
                    or any(left_start < right_end and right_start < left_end
                           for index, (left_start, left_end) in enumerate(own)
                           for right_start, right_end in own[index + 1:])
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
        if (not _trusted_root_metadata('/usr', stat.S_ISDIR) or
                not _trusted_root_metadata('/usr/bin', stat.S_ISDIR)):
            return False
        for path in paths:
            info = os.lstat(path)
            if not _root_metadata_trusted(info, stat.S_ISREG, executable=True):
                return False
            if path.endswith(("newuidmap", "newgidmap")) and not info.st_mode & stat.S_ISUID:
                return False
        return True
    except OSError:
        return False


def _root_metadata_trusted(info, kind, *, executable=False):
    """Classify stat data without consulting a daemon or following symlinks."""
    return (kind(info.st_mode) and info.st_uid == 0
            and not stat.S_IMODE(info.st_mode) & 0o022
            # Launcher is a shell script: UID1000 needs world read+execute,
            # not merely owner-root execute bits.
            and (not executable or (stat.S_IMODE(info.st_mode) & 0o005) == 0o005))


def _trusted_root_metadata(path, kind, *, executable=False):
    """Do not follow untrusted leaf symlinks or accept writable OS metadata."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return _root_metadata_trusted(info, kind, executable=executable)


def _login_rootless_launcher_present():
    """Presence only: a trusted launcher does not prove a running daemon."""
    return (_trusted_root_metadata('/usr', stat.S_ISDIR)
            and _trusted_root_metadata('/usr/bin', stat.S_ISDIR)
            and _trusted_root_metadata('/usr/bin/dockerd-rootless.sh',
                                       stat.S_ISREG, executable=True))


def _login_linger_marker_status(account):
    """Bounded, categorical lstat-only RCA: no loginctl, bus or mutations."""
    parents = ('/var', '/var/lib', '/var/lib/systemd', '/var/lib/systemd/linger')

    def status(path, kind):
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return 'MISSING'
        except OSError:
            return 'UNVERIFIED'
        return 'TRUSTED' if _root_metadata_trusted(info, kind) else 'UNTRUSTED'

    for parent in parents:
        result = status(parent, stat.S_ISDIR)
        if result != 'TRUSTED':
            return 'PARENT_' + result
    result = status(parents[-1] + '/' + account.pw_name, stat.S_ISREG)
    return 'TRUSTED' if result == 'TRUSTED' else 'MARKER_' + result


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
    linger_status = _login_linger_marker_status(account)
    linger_record = linger_status == 'TRUSTED'
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
        "user_linger_marker_status": linger_status,
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


def _local_passwd_records():
    """Use a trusted bounded local account map to cross-check NSS aliases."""
    try:
        raw = _login_subid_text('/etc/passwd')
        lines = raw.splitlines()
        if not lines or len(lines) > 1024:
            raise ValueError('LOCAL_PASSWD_BOUND')
        records = []
        for line in lines:
            fields = line.split(':')
            if (len(fields) != 7 or not fields[0] or len(line) > 1024 or
                    not re.fullmatch(r'[0-9]{1,10}', fields[2]) or
                    not re.fullmatch(r'[0-9]{1,10}', fields[3])):
                raise ValueError('LOCAL_PASSWD_FORMAT')
            records.append((fields[0], int(fields[2]), int(fields[3]), fields[5]))
        return records
    except (OSError, UnicodeError, ValueError):
        raise ProbeError('DEDICATED_PASSWD_UNVERIFIED') from None


def _local_group_records():
    """Cross-check the dedicated primary GID against trusted local group IDs."""
    try:
        lines = _login_subid_text('/etc/group').splitlines()
        if not lines or len(lines) > 1024:
            raise ValueError('LOCAL_GROUP_BOUND')
        records = []
        for line in lines:
            fields = line.split(':')
            if (len(fields) != 4 or len(line) > 1024 or
                    not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_.-]{0,79}', fields[0]) or
                    not re.fullmatch(r'[0-9]{1,10}', fields[2])):
                raise ValueError('LOCAL_GROUP_FORMAT')
            records.append((fields[0], int(fields[2])))
        return records
    except (OSError, UnicodeError, ValueError):
        raise ProbeError('DEDICATED_GROUP_UNVERIFIED') from None


def _dedicated_candidate(records, groups, login_gid):
    """Lookup candidate only; absence is NOT permission to create an account."""
    try:
        target = pwd.getpwnam(PRIVATE_ACCOUNT)
    except KeyError:
        # Orphan local group reservations and NSS/passwd disagreements must
        # not be advertised as a clean dedicated-account namespace.
        if (any(name == PRIVATE_ACCOUNT for name, _, _, _ in records) or
                any(name == PRIVATE_ACCOUNT for name, _ in groups)):
            return 'UNVERIFIED', None
        return 'ABSENT', None
    except OSError:
        return 'UNVERIFIED', None
    if (target.pw_name != PRIVATE_ACCOUNT or target.pw_uid <= CURRENT_LOGIN_EXPECTED_UID or
            target.pw_gid <= 1000 or target.pw_gid == login_gid or target.pw_dir not in (
                '/var/lib/' + PRIVATE_ACCOUNT, '/home/' + PRIVATE_ACCOUNT)):
        return 'UNVERIFIED', None
    try:
        by_uid = pwd.getpwuid(target.pw_uid)
        by_gid = grp.getgrgid(target.pw_gid)
        by_group_name = grp.getgrnam(PRIVATE_ACCOUNT)
    except (KeyError, OSError):
        return 'UNVERIFIED', None
    if (by_uid.pw_name != PRIVATE_ACCOUNT or by_uid.pw_uid != target.pw_uid or
            by_uid.pw_gid != target.pw_gid or by_uid.pw_dir != target.pw_dir or
            by_gid.gr_name != PRIVATE_ACCOUNT or by_gid.gr_gid != target.pw_gid or
            by_group_name.gr_name != PRIVATE_ACCOUNT or
            by_group_name.gr_gid != target.pw_gid):
        return 'UNVERIFIED', None
    matches = [(name, uid, gid, home) for name, uid, gid, home in records
               if name == PRIVATE_ACCOUNT or uid == target.pw_uid]
    group_matches = [(name, gid) for name, gid in groups
                     if name == PRIVATE_ACCOUNT or gid == target.pw_gid]
    if (matches != [(PRIVATE_ACCOUNT, target.pw_uid, target.pw_gid, target.pw_dir)] or
            group_matches != [(PRIVATE_ACCOUNT, target.pw_gid)]):
        return 'UNVERIFIED', None
    return 'PRESENT', target


def _dedicated_home_status(account):
    if account.pw_dir == '/var/lib/' + PRIVATE_ACCOUNT:
        parents = ('/var', '/var/lib')
    elif account.pw_dir == '/home/' + PRIVATE_ACCOUNT:
        parents = ('/home',)
    else:
        return 'UNVERIFIED'
    if not all(_trusted_root_metadata(path, stat.S_ISDIR) for path in parents):
        return 'UNVERIFIED'
    try:
        info = os.lstat(account.pw_dir)
    except FileNotFoundError:
        return 'MISSING'
    except OSError:
        return 'UNVERIFIED'
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != account.pw_uid or
            info.st_gid != account.pw_gid or stat.S_IMODE(info.st_mode) != 0o700):
        return 'UNSAFE'
    return 'PRIVATE'


def _absent_dedicated_home_status():
    """Account missing does not prove either reserved home path is unused."""
    for parent in ('/var', '/var/lib', '/home'):
        if not _trusted_root_metadata(parent, stat.S_ISDIR):
            return 'UNVERIFIED'
    observed = False
    for path in ('/var/lib/' + PRIVATE_ACCOUNT, '/home/' + PRIVATE_ACCOUNT):
        try:
            os.lstat(path)
        except FileNotFoundError:
            continue
        except OSError:
            return 'UNVERIFIED'
        observed = True
    return 'CANDIDATE_EXISTS' if observed else 'NOT_ASSIGNED'


def _read_proc_net(path):
    """Read only procfs net-table text; never create/inspect a socket."""
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('PROC_NET_TYPE')
        raw = bytearray()
        while True:
            chunk = os.read(fd, min(4096, MAX_PROC_NET_BYTES + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > MAX_PROC_NET_BYTES:
                raise ValueError('PROC_NET_BOUND')
        return bytes(raw)
    finally:
        os.close(fd)


def _parse_proc_net_port(raw, addr_length):
    if not isinstance(raw, bytes) or len(raw) > MAX_PROC_NET_BYTES:
        raise ValueError('PROC_NET_BOUND')
    lines = raw.decode('ascii', 'strict').splitlines()
    expected_header = ['sl', 'local_address', None, 'st', 'tx_queue',
                       'rx_queue', 'tr', 'tm->when', 'retrnsmt', 'uid',
                       'timeout', 'inode']
    header = lines[0].split() if lines else []
    if (len(lines) > 1025 or len(header) != len(expected_header) or
            header[2] not in ('rem_address', 'remote_address') or
            header[:2] != expected_header[:2] or
            header[3:] != expected_header[3:]):
        raise ValueError('PROC_NET_FORMAT')
    present = False
    for line in lines[1:]:
        columns = line.split()
        if len(columns) < 12 or not re.fullmatch(r'[0-9]+:', columns[0]):
            raise ValueError('PROC_NET_FORMAT')
        local = columns[1].split(':')
        remote = columns[2].split(':')
        if (len(local) != 2 or len(remote) != 2 or
                any(not re.fullmatch(r'[0-9A-Fa-f]{' + str(addr_length) + r'}', part[0])
                    or not re.fullmatch(r'[0-9A-Fa-f]{4}', part[1])
                    for part in (local, remote)) or
                not re.fullmatch(r'[0-9A-Fa-f]{2}', columns[3]) or
                int(columns[3], 16) not in range(1, 13) or
                not re.fullmatch(r'[0-9A-Fa-f]{8}:[0-9A-Fa-f]{8}', columns[4]) or
                not re.fullmatch(r'[0-9A-Fa-f]{2}:[0-9A-Fa-f]{8}', columns[5]) or
                not re.fullmatch(r'[0-9A-Fa-f]{8}', columns[6]) or
                any(not re.fullmatch(r'[0-9]{1,20}', field)
                    for field in (columns[7],columns[8],columns[9],columns[10])) or
                not re.fullmatch(r'[0-9A-Fa-f]{1,16}', columns[11]) or
                any(not (index == 16 and field == '-1') and
                    not re.fullmatch(r'[0-9A-Fa-f]{1,16}', field)
                    for index, field in enumerate(columns[12:], 12))):
            raise ValueError('PROC_NET_FORMAT')
        # Linux proc_net_tcp documents signed -1 ONLY for ssthresh (field 17,
        # zero-based 16); accepting it elsewhere would mask malformed rows.
        # The kernel's TIME_WAIT short format has 12 fields; other states
        # require the full 17-field extension to avoid a false empty proof.
        if int(columns[3], 16) != 6 and len(columns) < 17:
            raise ValueError('PROC_NET_FORMAT')
        if int(local[1], 16) == 8000 and columns[3].upper() == '0A':
            present = True
    return present


def _port_8000_listener():
    """Bounded host-netns observation and fixed failure stage, never port ownership."""
    netns = []
    # Fixed sequential attempts. On the first failure, the other marker
    # remains unobserved; never imply both failed or disclose raw errno/path.
    for path, stage in (('/proc/1/ns/net', 'PID1'),
                        ('/proc/self/ns/net', 'SELF')):
        try:
            netns.append(os.stat(path))
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EPERM):
                category = 'ACCESS_DENIED'
            elif exc.errno in (errno.ENOENT, errno.ENOTDIR):
                category = 'NOT_FOUND'
            else:
                category = 'OTHER_OSERROR'
            return 'UNVERIFIED', 'NETNS_' + stage + '_' + category
        except (UnicodeError, ValueError):
            return 'UNVERIFIED', 'NETNS_' + stage + '_OTHER_EXCEPTION'
    pid1, current = netns
    if (pid1.st_dev, pid1.st_ino) != (current.st_dev, current.st_ino):
        return 'UNVERIFIED', 'NETNS_MISMATCH'
    listening = False
    for path, length, reason_prefix in (
            ('/proc/self/net/tcp', 8, 'TCP4'),
            ('/proc/self/net/tcp6', 32, 'TCP6')):
        try:
            raw = _read_proc_net(path)
        except (OSError, UnicodeError, ValueError):
            return 'UNVERIFIED', reason_prefix + '_READ_UNVERIFIED'
        try:
            listening |= _parse_proc_net_port(raw, length)
        except (OSError, UnicodeError, ValueError):
            return 'UNVERIFIED', reason_prefix + '_PARSE_UNVERIFIED'
    return ('LISTEN_PRESENT' if listening else 'NONE_OBSERVED'), 'NONE'


def collect_dedicated_preflight():
    """UID1000 nonroot prerequisites only; no Docker/sudo/systemctl or API."""
    if os.geteuid() != CURRENT_LOGIN_EXPECTED_UID:
        raise ProbeError('DEDICATED_LOGIN_UNVERIFIED')
    try:
        login = pwd.getpwuid(CURRENT_LOGIN_EXPECTED_UID)
        by_name = pwd.getpwnam(DEDICATED_LOGIN)
        records = _local_passwd_records()
        groups = _local_group_records()
    except (KeyError, OSError):
        raise ProbeError('DEDICATED_LOGIN_UNVERIFIED') from None
    if (login.pw_name != DEDICATED_LOGIN or login.pw_uid != CURRENT_LOGIN_EXPECTED_UID or
            login.pw_gid < 1000 or login.pw_dir != '/home/' + DEDICATED_LOGIN or
            by_name.pw_name != DEDICATED_LOGIN or
            by_name.pw_uid != CURRENT_LOGIN_EXPECTED_UID or
            by_name.pw_gid != login.pw_gid or by_name.pw_dir != login.pw_dir or
            [record for record in records if record[0] == DEDICATED_LOGIN or
             record[1] == CURRENT_LOGIN_EXPECTED_UID] != [
                 (DEDICATED_LOGIN, CURRENT_LOGIN_EXPECTED_UID,
                  login.pw_gid, login.pw_dir)] or
            not _trusted_root_metadata('/home', stat.S_ISDIR)):
        raise ProbeError('DEDICATED_LOGIN_UNVERIFIED')
    info = _lstat(login.pw_dir, 'DEDICATED_LOGIN_UNVERIFIED')
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != CURRENT_LOGIN_EXPECTED_UID
            or info.st_gid != login.pw_gid or stat.S_IMODE(info.st_mode) & 0o022):
        raise ProbeError('DEDICATED_LOGIN_UNVERIFIED')
    account_state, candidate = _dedicated_candidate(records, groups, login.pw_gid)
    home = (_absent_dedicated_home_status() if account_state == 'ABSENT'
            else 'UNVERIFIED')
    # Even an absent passwd/NSS account can have orphaned subuid/subgid ranges.
    # Without a unique assigned UID, never infer those maps are free.
    subids = 'UNVERIFIED'
    uid_state = 'NOT_ASSIGNED' if account_state == 'ABSENT' else 'UNVERIFIED'
    socket_state = 'NOT_ASSIGNED' if account_state == 'ABSENT' else 'UNVERIFIED'
    if candidate is not None:
        uid_state = 'DISTINCT'
        home = _dedicated_home_status(candidate)
        subids = 'AVAILABLE' if _login_subids_ready(candidate) else 'UNVERIFIED'
        # UID1000 cannot inspect another account's 0700 /run/user path;
        # in particular, no attempted read/connect to foreign Docker socket.
        socket_state = 'NOT_INSPECTED'
    port_status, port_reason = _port_8000_listener()
    return {
        'schema': DEDICATED_PREFLIGHT_SCHEMA, 'state': 'NO_VERIFICADO',
        'read_only': True, 'mutations': 0, 'docker_queries': 0,
        # NSS may use external transports; this probe only promises that
        # it does not itself open/connect to sockets. No false NSS claim.
        'direct_socket_connections': 0, 'nss_socket_activity': 'UNVERIFIED',
        'systemctl_queries': 0, 'sudo_executed': False,
        'rootless_certified': False,
        'dedicated_account': account_state, 'dedicated_uid': uid_state,
        'dedicated_home': home, 'dedicated_subids': subids,
        'dedicated_socket': socket_state,
        'rootless_toolchain': ('AVAILABLE' if _login_rootless_toolchain()
                               else 'UNVERIFIED'),
        'rootless_launcher': ('AVAILABLE' if _login_rootless_launcher_present()
                              else 'UNVERIFIED'),
        'user_namespace': 'AVAILABLE' if _login_userns_ready() else 'UNVERIFIED',
        'port_8000_listen': port_status,
        'port_8000_reason': port_reason,
    }


def main_dedicated_preflight():
    try:
        receipt = collect_dedicated_preflight()
    except ProbeError as exc:
        receipt = {
            'schema': DEDICATED_PREFLIGHT_SCHEMA, 'state': 'NO_VERIFICADO',
            'read_only': True, 'mutations': 0, 'docker_queries': 0,
            'direct_socket_connections': 0, 'nss_socket_activity': 'UNVERIFIED',
            'systemctl_queries': 0, 'sudo_executed': False,
            'rootless_certified': False, 'reason': exc.reason,
        }
    except Exception:
        receipt = {
            'schema': DEDICATED_PREFLIGHT_SCHEMA, 'state': 'NO_VERIFICADO',
            'read_only': True, 'mutations': 0, 'docker_queries': 0,
            'direct_socket_connections': 0, 'nss_socket_activity': 'UNVERIFIED',
            'systemctl_queries': 0, 'sudo_executed': False,
            'rootless_certified': False, 'reason': 'DEDICATED_PROBE_UNVERIFIED',
        }
    print(json.dumps(receipt, sort_keys=True, separators=(',', ':')))
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
    if sys.argv[1:] == ["--dedicated-preflight"]:
        raise SystemExit(main_dedicated_preflight())
    if len(sys.argv) != 1:
        raise SystemExit(79)
    raise SystemExit(main())
