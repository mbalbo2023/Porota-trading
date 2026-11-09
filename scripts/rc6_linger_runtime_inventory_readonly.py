"""Bounded nonroot systemd-user autostart inventory; NOT rootless certification.

The SSH caller streams this file to the already authenticated UID-1000 login.
Only aggregate categories leave that login; unit names, paths and raw command
errors are never printed. No privileged or Docker operations are performed.
"""

import json
import os
import pwd
import re
import selectors
import stat
import subprocess
import sys
import time


SCHEMA = 'rc6.r1.linger-runtime-inventory-readonly.v1'
ACCOUNT = 'porotaadmin'
UID = 1000
RUNTIME = '/run/user/1000'
BUS = RUNTIME + '/bus'
UNIT = re.compile(r'[A-Za-z0-9_.@:-]+\.(?:service|socket|timer)\Z')
REASON_ALLOWLIST = frozenset((
    'OS_IDENTITY_UNVERIFIED', 'OS_BINARY_UNVERIFIED',
    'LOGIN_IDENTITY_UNVERIFIED', 'USER_BUS_UNVERIFIED',
    'QUERY_BOUND_INVALID', 'QUERY_TIMEOUT', 'QUERY_OUTPUT_BOUND',
    'QUERY_FAILED', 'QUERY_UNVERIFIED', 'SYSTEMD_OUTPUT_UNVERIFIED',
    'LOGIND_UNVERIFIED', 'INVENTORY_UNVERIFIED'))
KNOWN_UNIT_STATES = frozenset(('enabled', 'enabled-runtime', 'disabled',
    'static', 'indirect', 'alias', 'masked', 'masked-runtime', 'generated',
    'transient', 'linked', 'linked-runtime', 'bad'))
KNOWN_ACTIVE_STATES = frozenset(('active', 'inactive', 'activating',
    'deactivating', 'reloading', 'failed'))


class InventoryHold(Exception):
    """Fail-closed with only a fixed, public-safe error category."""

    def __init__(self, reason, phase='PREFLIGHT'):
        super().__init__(reason)
        self.phase = phase


def _trusted_directory(path, owner, private=False):
    try:
        info = os.lstat(path)
    except OSError:
        raise InventoryHold('OS_IDENTITY_UNVERIFIED') from None
    mode = stat.S_IMODE(info.st_mode)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or
            mode & 0o022 or (private and mode != 0o700)):
        raise InventoryHold('OS_IDENTITY_UNVERIFIED')


def _trusted_executable(path):
    try:
        info = os.lstat(path)
    except OSError:
        raise InventoryHold('OS_BINARY_UNVERIFIED') from None
    mode = stat.S_IMODE(info.st_mode)
    # Root-owned, no group/other write, executable by UID 1000 (the other bit).
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or
            mode & 0o022 or not mode & 0o001):
        raise InventoryHold('OS_BINARY_UNVERIFIED')


def _preflight():
    if os.geteuid() != UID:
        raise InventoryHold('LOGIN_IDENTITY_UNVERIFIED')
    try:
        account = pwd.getpwuid(UID)
        same_name = [item for item in pwd.getpwall()
                     if item.pw_name == ACCOUNT or item.pw_uid == UID]
    except (KeyError, OSError):
        raise InventoryHold('LOGIN_IDENTITY_UNVERIFIED') from None
    if (account.pw_name != ACCOUNT or account.pw_uid != UID or
            len(same_name) != 1 or same_name[0].pw_name != ACCOUNT or
            same_name[0].pw_uid != UID):
        raise InventoryHold('LOGIN_IDENTITY_UNVERIFIED')
    _trusted_directory('/usr', 0)
    _trusted_directory('/usr/bin', 0)
    _trusted_directory(RUNTIME, UID, private=True)
    try:
        socket = os.lstat(BUS)
    except OSError:
        raise InventoryHold('USER_BUS_UNVERIFIED') from None
    if (not stat.S_ISSOCK(socket.st_mode) or socket.st_uid != UID or
            stat.S_IMODE(socket.st_mode) & 0o007):
        raise InventoryHold('USER_BUS_UNVERIFIED')
    for path in ('/usr/bin/loginctl', '/usr/bin/systemctl'):
        _trusted_executable(path)


def _run_bounded_command(argv, *, env, timeout, max_bytes):
    """Capture at most max_bytes without spilling raw stdout/stderr to CI."""
    if not (0 < timeout <= 5 and 0 < max_bytes <= 16384):
        raise InventoryHold('QUERY_BOUND_INVALID')
    child = None
    selector = selectors.DefaultSelector()
    try:
        child = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=env, close_fds=True)
        selector.register(child.stdout, selectors.EVENT_READ)
        until = time.monotonic() + timeout
        chunks = bytearray()
        while selector.get_map():
            remaining = until - time.monotonic()
            if remaining <= 0:
                raise InventoryHold('QUERY_TIMEOUT')
            for key, _ in selector.select(remaining):
                block = os.read(key.fileobj.fileno(), min(4096, max_bytes + 1 - len(chunks)))
                if not block:
                    selector.unregister(key.fileobj)
                    break
                chunks.extend(block)
                if len(chunks) > max_bytes:
                    raise InventoryHold('QUERY_OUTPUT_BOUND')
        remaining = until - time.monotonic()
        if remaining <= 0:
            raise InventoryHold('QUERY_TIMEOUT')
        if child.wait(timeout=remaining) != 0:
            raise InventoryHold('QUERY_FAILED')
        return bytes(chunks)
    except (OSError, subprocess.SubprocessError):
        raise InventoryHold('QUERY_UNVERIFIED') from None
    finally:
        selector.close()
        if child is not None:
            if child.poll() is None:
                child.kill()
            child.wait()
            if child.stdout is not None:
                child.stdout.close()


def _decoded(raw):
    if not isinstance(raw, bytes) or len(raw) > 16384:
        raise InventoryHold('SYSTEMD_OUTPUT_UNVERIFIED')
    try:
        return raw.decode('utf-8', 'strict').splitlines()
    except UnicodeError:
        raise InventoryHold('SYSTEMD_OUTPUT_UNVERIFIED') from None


def _unit_files(raw):
    present = False
    for line in _decoded(raw):
        line = line.strip()
        if not line or re.fullmatch(r'\d+ unit files listed\.', line):
            continue
        columns = line.split()
        if (len(columns) not in (2, 3) or not UNIT.fullmatch(columns[0]) or
                columns[1] not in KNOWN_UNIT_STATES):
            raise InventoryHold('SYSTEMD_OUTPUT_UNVERIFIED')
        if columns[1] in ('enabled', 'enabled-runtime'):
            present = True
    return 'PRESENT' if present else 'NONE_OBSERVED'


def _loaded_units(raw):
    active_services = timers = sockets = False
    for line in _decoded(raw):
        line = line.strip()
        if not line or re.fullmatch(r'\d+ loaded units listed\.', line):
            continue
        columns = line.split(None, 4)
        if (len(columns) < 4 or not UNIT.fullmatch(columns[0]) or
                columns[1] not in ('loaded','not-found','bad','masked','error') or
                columns[2] not in KNOWN_ACTIVE_STATES):
            raise InventoryHold('SYSTEMD_OUTPUT_UNVERIFIED')
        name, _, active = columns[:3]
        if name.endswith('.service') and active != 'inactive':
            active_services = True
        if name.endswith('.timer'):
            timers = True
        if name.endswith('.socket'):
            sockets = True
    return tuple('PRESENT' if hit else 'NONE_OBSERVED' for hit in
                 (active_services, timers, sockets))


def _dependencies(raw):
    lines = [line.strip() for line in _decoded(raw) if line.strip()]
    if not lines or lines[0] != 'default.target' or len(lines) > 128:
        raise InventoryHold('SYSTEMD_OUTPUT_UNVERIFIED')
    for line in lines[1:]:
        if not re.fullmatch(r'[A-Za-z0-9_.@:-]+\.[A-Za-z]+', line):
            raise InventoryHold('SYSTEMD_OUTPUT_UNVERIFIED')
    return 'PRESENT' if len(lines) > 1 else 'NONE_OBSERVED'


def collect():
    _preflight()
    # Fixed argument vectors, restricted environment and explicit systemd
    # user-bus reads. Does NOT touch a Docker or arbitrary AF_UNIX endpoint.
    env = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C',
           'XDG_RUNTIME_DIR': RUNTIME,
           'DBUS_SESSION_BUS_ADDRESS': 'unix:path=' + BUS}
    commands = (
        ('/usr/bin/loginctl', '--no-pager', 'show-user', '--property=Linger', ACCOUNT),
        ('/usr/bin/systemctl', '--user', '--no-pager', '--no-legend',
         'list-unit-files', '--all', '--type=service,socket,timer'),
        ('/usr/bin/systemctl', '--user', '--no-pager', '--no-legend',
         'list-units', '--all', '--type=service,socket,timer'),
        ('/usr/bin/systemctl', '--user', '--no-pager', '--no-legend', '--plain',
         'list-dependencies', '--all', 'default.target'),
    )
    outputs = []
    for phase, command in zip(('LOGIND_QUERY', 'UNIT_FILES_QUERY',
                               'ACTIVE_UNITS_QUERY', 'TARGET_DEPS_QUERY'), commands):
        try:
            outputs.append(_run_bounded_command(command, env=env, timeout=5,
                                                max_bytes=16384))
        except InventoryHold as exc:
            raise InventoryHold(str(exc), phase) from None
    try:
        linger = _decoded(outputs[0])
        if linger not in (['Linger=yes'], ['Linger=no']):
            raise InventoryHold('LOGIND_UNVERIFIED')
    except InventoryHold as exc:
        raise InventoryHold(str(exc), 'LOGIND_PARSE') from None
    try:
        enabled = _unit_files(outputs[1])
    except InventoryHold as exc:
        raise InventoryHold(str(exc), 'UNIT_FILES_PARSE') from None
    try:
        active, timers, sockets = _loaded_units(outputs[2])
    except InventoryHold as exc:
        raise InventoryHold(str(exc), 'ACTIVE_UNITS_PARSE') from None
    try:
        deps = _dependencies(outputs[3])
    except InventoryHold as exc:
        raise InventoryHold(str(exc), 'TARGET_DEPS_PARSE') from None
    risk = ('PRESENT' if 'PRESENT' in (enabled, active, timers, sockets, deps)
            else 'UNVERIFIED')
    return {'schema': SCHEMA, 'state': 'NO_VERIFICADO', 'read_only': True,
            'linger_effective': 'YES' if linger[0] == 'Linger=yes' else 'NO',
            'enabled_user_units': enabled, 'active_user_services': active,
            'user_timers': timers, 'user_sockets': sockets,
            'default_target_dependencies': deps, 'autostart_risk': risk,
            'systemd_dbus_state': 'QUERIED_READONLY', 'mutations': 0,
            'docker_queries': 0, 'docker_socket_connections': 0,
            'sudo_executed': False, 'rootless_certified': False}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv != ['--linger-inventory']:
        return 79
    try:
        result = collect()
    except InventoryHold as exc:
        # Safe enum only; host exceptions and raw systemd output are never sent.
        reason = str(exc)
        if reason not in REASON_ALLOWLIST:
            reason = 'INVENTORY_UNVERIFIED'
            phase = 'INVENTORY_UNKNOWN'
        else:
            phase = exc.phase if exc.phase in ('PREFLIGHT', 'LOGIND_QUERY',
            'UNIT_FILES_QUERY', 'ACTIVE_UNITS_QUERY', 'TARGET_DEPS_QUERY',
            'LOGIND_PARSE', 'UNIT_FILES_PARSE', 'ACTIVE_UNITS_PARSE',
            'TARGET_DEPS_PARSE') else 'INVENTORY_UNKNOWN'
        result = {'schema': SCHEMA, 'state': 'NO_VERIFICADO',
                  'reason': reason, 'phase': phase, 'read_only': True, 'mutations': 0,
                  'docker_queries': 0, 'docker_socket_connections': 0,
                  'sudo_executed': False, 'rootless_certified': False}
    except Exception:
        result = {'schema': SCHEMA, 'state': 'NO_VERIFICADO',
                  'reason': 'INVENTORY_UNVERIFIED',
                  'phase': 'INVENTORY_UNKNOWN', 'read_only': True,
                  'mutations': 0, 'docker_queries': 0,
                  'docker_socket_connections': 0, 'sudo_executed': False,
                  'rootless_certified': False}
    print(json.dumps(result, sort_keys=True, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
