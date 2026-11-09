"""Bounded nonroot systemd-user autostart inventory; NOT rootless certification.

The SSH caller streams this file to the already authenticated UID-1000 login.
Only aggregate categories leave that login; unit names, paths and raw command
errors are never printed. No privileged or Docker operations are performed.
"""

import errno
import json
import os
import pwd
import re
import selectors
import stat
import subprocess
import sys
import time


SCHEMA = 'rc6.r1.linger-runtime-inventory-readonly.v2'
ACCOUNT = 'porotaadmin'
UID = 1000
RUNTIME = '/run/user/1000'
BUS = RUNTIME + '/bus'
USER_UNIT_TYPES = frozenset(('service', 'socket', 'timer'))
ALL_UNIT_TYPES = frozenset(('service', 'socket', 'device', 'mount',
    'automount', 'swap', 'target', 'path', 'timer', 'slice', 'scope'))
UNIT_NAME_CHARS = frozenset('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
    '0123456789:_.-@')
HEX_CHARS = frozenset('0123456789abcdefABCDEF')
REASON_ALLOWLIST = frozenset((
    'OS_IDENTITY_UNVERIFIED', 'OS_BINARY_UNVERIFIED',
    'LOGIN_IDENTITY_UNVERIFIED', 'USER_BUS_UNVERIFIED',
    'QUERY_BOUND_INVALID', 'QUERY_TIMEOUT', 'QUERY_OUTPUT_BOUND',
    'QUERY_FAILED', 'QUERY_UNVERIFIED', 'SYSTEMD_OUTPUT_UNVERIFIED',
    'LOGIND_UNVERIFIED', 'INVENTORY_UNVERIFIED',
    'UNIT_FILES_ENCODING', 'UNIT_FILES_COLUMNS', 'UNIT_FILES_NAME',
    'UNIT_FILES_STATE', 'ACTIVE_UNITS_ENCODING', 'ACTIVE_UNITS_COLUMNS',
    'ACTIVE_UNITS_NAME', 'ACTIVE_UNITS_LOAD', 'ACTIVE_UNITS_ACTIVE',
    'TARGET_DEPS_ENCODING', 'TARGET_DEPS_EMPTY', 'TARGET_DEPS_ROOT', 'TARGET_DEPS_BOUND',
    'TARGET_DEPS_NAME'))
KNOWN_UNIT_STATES = frozenset(('enabled', 'enabled-runtime', 'disabled',
    'static', 'indirect', 'alias', 'masked', 'masked-runtime', 'generated',
    'transient', 'linked', 'linked-runtime', 'bad'))
KNOWN_ACTIVE_STATES = frozenset(('active', 'inactive', 'activating',
    'deactivating', 'reloading', 'failed', 'maintenance', 'refreshing'))
# systemctl(1): `bad` is a UNIT FILE state; LOAD uses `bad-setting`.
KNOWN_LOAD_STATES = frozenset(('loaded', 'not-found',
    'bad-setting', 'masked', 'error'))
METADATA_STATES = frozenset(('MISSING', 'ACCESS_DENIED', 'SYMLINK',
    'WRONG_TYPE', 'WRONG_OWNER', 'UNSAFE_MODE', 'METADATA_VALID', 'UNVERIFIED'))
BUS_OTHER_BITS = frozenset(('PRESENT', 'ABSENT', 'NOT_CHECKED'))
LOGIND_STATES = frozenset(('NOT_QUERIED', 'PARSED_READONLY',
    'QUERY_UNVERIFIED', 'PARSE_UNVERIFIED'))
UNIT_FIELDS = ('enabled_user_units', 'active_user_services', 'user_timers',
               'user_sockets', 'default_target_dependencies')


class InventoryHold(Exception):
    """Fail-closed with only a fixed, public-safe error category."""

    def __init__(self, reason, phase='PREFLIGHT', *, linger='UNVERIFIED',
                 user_runtime_status='UNVERIFIED', user_bus_status='NOT_CHECKED',
                 bus_mode_other_bits='NOT_CHECKED', logind_status='NOT_QUERIED',
                 systemd_dbus_state='NOT_QUERIED'):
        super().__init__(reason)
        self.phase = phase
        self.linger = linger
        self.user_runtime_status = user_runtime_status
        self.user_bus_status = user_bus_status
        self.bus_mode_other_bits = bus_mode_other_bits
        self.logind_status = logind_status
        self.systemd_dbus_state = systemd_dbus_state


def _trusted_directory(path, owner, private=False):
    try:
        info = os.lstat(path)
    except OSError:
        raise InventoryHold('OS_IDENTITY_UNVERIFIED') from None
    mode = stat.S_IMODE(info.st_mode)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or
            mode & 0o022 or (private and mode != 0o700)):
        raise InventoryHold('OS_IDENTITY_UNVERIFIED')
    return (info.st_dev, info.st_ino, mode)


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
    # The trusted parents make ENOENT at the UID runtime or bus meaningful.
    # lstat also rejects a symlink before any of its children can be trusted.
    run_identity = _trusted_directory('/run', 0)
    user_parent_identity = _trusted_directory('/run/user', 0)
    _trusted_executable('/usr/bin/loginctl')
    return run_identity, user_parent_identity


def _metadata(path, *, directory):
    """Categorize one lstat safely; no raw host metadata exits the process."""
    try:
        info = os.lstat(path)
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return 'MISSING', 'NOT_CHECKED', None
        if exc.errno in (errno.EACCES, errno.EPERM):
            return 'ACCESS_DENIED', 'NOT_CHECKED', None
        return 'UNVERIFIED', 'NOT_CHECKED', None
    if stat.S_ISLNK(info.st_mode):
        return 'SYMLINK', 'NOT_CHECKED', None
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISSOCK(info.st_mode)):
        return 'WRONG_TYPE', 'NOT_CHECKED', None
    if info.st_uid != UID:
        return 'WRONG_OWNER', 'NOT_CHECKED', None
    mode = stat.S_IMODE(info.st_mode)
    identity = (info.st_dev, info.st_ino, mode)
    if directory:
        return ('METADATA_VALID' if mode == 0o700 else 'UNSAFE_MODE'), 'NOT_CHECKED', identity
    # Security+SRE+Architecture: even 0666 is valid AF_UNIX metadata when
    # protected by a real, UID-owned 0700 runtime parent. Other bits are
    # reported, not treated as authentication or a Docker/host permission.
    other = 'PRESENT' if mode & 0o007 else 'ABSENT'
    if (mode & 0o7000 or not mode & 0o200):
        return 'UNSAFE_MODE', other, identity
    return 'METADATA_VALID', other, identity


def _recheck_user_bus(parent_ids, runtime_id, bus_id, context, *, phase,
                      prior_user_queries):
    """Catch observable swaps; preserve whether user-systemd was queried."""
    def deny(**statuses):
        # A drift before the first systemctl is a preflight failure; a drift
        # between calls is an incomplete inventory, NEVER 'NOT_QUERIED'.
        raise InventoryHold('USER_BUS_UNVERIFIED',
            phase if prior_user_queries else 'PREFLIGHT',
            systemd_dbus_state=('INCOMPLETE_READONLY' if prior_user_queries
                                else 'NOT_QUERIED'),
            **{**context, **statuses})

    try:
        parent_now = (_trusted_directory('/run', 0),
                      _trusted_directory('/run/user', 0))
    except InventoryHold:
        parent_now = None
    if parent_now != parent_ids:
        deny(user_runtime_status='UNVERIFIED', user_bus_status='NOT_CHECKED',
             bus_mode_other_bits='NOT_CHECKED')
    runtime_status, _, new_runtime_id = _metadata(RUNTIME, directory=True)
    if runtime_status != 'METADATA_VALID' or new_runtime_id != runtime_id:
        deny(user_runtime_status=(runtime_status if runtime_status != 'METADATA_VALID'
                                  else 'UNVERIFIED'),
             user_bus_status='NOT_CHECKED', bus_mode_other_bits='NOT_CHECKED')
    bus_status, bits, new_bus_id = _metadata(BUS, directory=False)
    if bus_status != 'METADATA_VALID' or new_bus_id != bus_id:
        deny(user_bus_status=(bus_status if bus_status != 'METADATA_VALID'
                              else 'UNVERIFIED'),
             bus_mode_other_bits=(bits if bus_status == 'UNSAFE_MODE'
                                  else 'NOT_CHECKED'))


def _fixed_receipt():
    return {'schema': SCHEMA, 'state': 'NO_VERIFICADO', 'read_only': True,
            'mutations': 0, 'docker_queries': 0,
            'docker_socket_connections': 0, 'sudo_executed': False,
            'rootless_certified': False}


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


def _valid_unit_name(name, suffixes):
    """Conservative ASCII systemd name subset; no decoding or output of names.

    systemd.unit(5): <=255 ASCII characters including suffix, 11 suffixes,
    literal C-style \\xHH escaping, optional @ template/instance. This is
    intentionally NOT a claim of full upstream parser equivalence. Literal
    escapes for byte 01..FF (including C0/DEL) are accepted but NEVER
    decoded; actual control characters and NUL escapes are rejected.
    """
    if (type(name) is not str or not name.isascii() or
            not 1 <= len(name) <= 255 or '.' not in name):
        return False
    prefix, suffix = name.rsplit('.', 1)
    if not prefix or prefix[0] == '@' or suffix not in suffixes:
        return False
    index = 0
    while index < len(prefix):
        char = prefix[index]
        if char == '\\':
            pair = prefix[index + 2:index + 4]
            if (prefix[index + 1:index + 2] != 'x' or len(pair) != 2 or
                    any(digit not in HEX_CHARS for digit in pair)):
                return False
            byte_value = int(pair, 16)
            if byte_value == 0:
                return False
            index += 4
        elif char in UNIT_NAME_CHARS:
            index += 1
        else:
            return False
    return True


def _unit_files(raw):
    present = False
    try:
        lines = _decoded(raw)
    except InventoryHold:
        raise InventoryHold('UNIT_FILES_ENCODING') from None
    for line in lines:
        line = line.strip()
        if not line or re.fullmatch(r'\d+ unit files listed\.', line):
            continue
        columns = line.split()
        if len(columns) not in (2, 3):
            raise InventoryHold('UNIT_FILES_COLUMNS')
        if not _valid_unit_name(columns[0], USER_UNIT_TYPES):
            raise InventoryHold('UNIT_FILES_NAME')
        if columns[1] not in KNOWN_UNIT_STATES:
            raise InventoryHold('UNIT_FILES_STATE')
        # The optional third column is a preset, NOT validated or certified
        # by this scope. No new unit-file states/rows are admitted.
        if columns[1] in ('enabled', 'enabled-runtime'):
            present = True
    return 'PRESENT' if present else 'NONE_OBSERVED'


def _loaded_units(raw):
    active_services = timers = sockets = False
    try:
        lines = _decoded(raw)
    except InventoryHold:
        raise InventoryHold('ACTIVE_UNITS_ENCODING') from None
    for line in lines:
        line = line.strip()
        if not line or re.fullmatch(r'\d+ loaded units listed\.', line):
            continue
        columns = line.split(None, 4)
        if len(columns) < 4:
            raise InventoryHold('ACTIVE_UNITS_COLUMNS')
        if not _valid_unit_name(columns[0], USER_UNIT_TYPES):
            raise InventoryHold('ACTIVE_UNITS_NAME')
        if columns[1] not in KNOWN_LOAD_STATES:
            raise InventoryHold('ACTIVE_UNITS_LOAD')
        if columns[2] not in KNOWN_ACTIVE_STATES:
            raise InventoryHold('ACTIVE_UNITS_ACTIVE')
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
    try:
        lines = [line.strip() for line in _decoded(raw) if line.strip()]
    except InventoryHold:
        raise InventoryHold('TARGET_DEPS_ENCODING') from None
    if not lines:
        raise InventoryHold('TARGET_DEPS_EMPTY')
    if lines[0] != 'default.target':
        raise InventoryHold('TARGET_DEPS_ROOT')
    # Maximum 128 nonblank rows including default.target, fail closed.
    if len(lines) > 128:
        raise InventoryHold('TARGET_DEPS_BOUND')
    for line in lines[1:]:
        if not _valid_unit_name(line, ALL_UNIT_TYPES):
            raise InventoryHold('TARGET_DEPS_NAME')
    return 'PRESENT' if len(lines) > 1 else 'NONE_OBSERVED'


def collect():
    parent_ids = _preflight()
    runtime_status, _, runtime_id = _metadata(RUNTIME, directory=True)
    if runtime_status == 'METADATA_VALID':
        bus_status, bus_other_bits, bus_id = _metadata(BUS, directory=False)
    else:
        bus_status, bus_other_bits = 'NOT_CHECKED', 'NOT_CHECKED'
        bus_id = None
    metadata = {'user_runtime_status': runtime_status,
                'user_bus_status': bus_status,
                'bus_mode_other_bits': bus_other_bits}

    # Independently read the system bus through a trusted root-owned loginctl
    # BEFORE considering a user-bus connection. No DBUS_SESSION_BUS_ADDRESS
    # or XDG_RUNTIME_DIR is injected for the system-bus-only query.
    logind_env = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}
    linger = 'UNVERIFIED'
    logind_status = 'NOT_QUERIED'
    logind_hold = None
    try:
        raw_linger = _run_bounded_command(
            ('/usr/bin/loginctl', '--no-pager', 'show-user', '--property=Linger', ACCOUNT),
            env=logind_env, timeout=5, max_bytes=16384)
    except InventoryHold as exc:
        logind_status = 'QUERY_UNVERIFIED'
        logind_hold = (str(exc), 'LOGIND_QUERY')
    else:
        try:
            lines = _decoded(raw_linger)
            if lines not in (['Linger=yes'], ['Linger=no']):
                raise InventoryHold('LOGIND_UNVERIFIED')
            linger = 'YES' if lines[0] == 'Linger=yes' else 'NO'
            logind_status = 'PARSED_READONLY'
        except InventoryHold as exc:
            logind_status = 'PARSE_UNVERIFIED'
            logind_hold = (str(exc), 'LOGIND_PARSE')
    context = {**metadata, 'linger': linger, 'logind_status': logind_status}
    if runtime_status != 'METADATA_VALID' or bus_status != 'METADATA_VALID':
        # An invalid user bus does not erase independently observed linger.
        # No systemctl --user may be invoked on this path.
        raise InventoryHold('USER_BUS_UNVERIFIED', 'PREFLIGHT', **context)
    if logind_hold is not None:
        raise InventoryHold(*logind_hold, **context)
    try:
        _trusted_executable('/usr/bin/systemctl')
    except InventoryHold as exc:
        raise InventoryHold(str(exc), 'PREFLIGHT', **context) from None

    # Fixed argument vectors, bounded systemd user-bus reads. The socket
    # metadata is not a positive authentication or rootless certification.
    user_env = {**logind_env, 'XDG_RUNTIME_DIR': RUNTIME,
                'DBUS_SESSION_BUS_ADDRESS': 'unix:path=' + BUS}
    commands = (
        ('/usr/bin/systemctl', '--user', '--no-pager', '--no-legend',
         'list-unit-files', '--all', '--type=service,socket,timer'),
        ('/usr/bin/systemctl', '--user', '--no-pager', '--no-legend',
         '--plain', '--full', 'list-units', '--all', '--type=service,socket,timer'),
        ('/usr/bin/systemctl', '--user', '--no-pager', '--no-legend', '--plain', '--full',
         'list-dependencies', '--all', 'default.target'),
    )
    outputs = []
    for phase, command in zip(('UNIT_FILES_QUERY',
                               'ACTIVE_UNITS_QUERY', 'TARGET_DEPS_QUERY'), commands):
        _recheck_user_bus(parent_ids, runtime_id, bus_id, context,
                          phase=phase, prior_user_queries=bool(outputs))
        try:
            outputs.append(_run_bounded_command(command, env=user_env, timeout=5,
                                                max_bytes=16384))
        except InventoryHold as exc:
            raise InventoryHold(str(exc), phase,
                                systemd_dbus_state='INCOMPLETE_READONLY',
                                **context) from None
    try:
        enabled = _unit_files(outputs[0])
    except InventoryHold as exc:
        raise InventoryHold(str(exc), 'UNIT_FILES_PARSE',
                            systemd_dbus_state='INCOMPLETE_READONLY',
                            **context) from None
    try:
        active, timers, sockets = _loaded_units(outputs[1])
    except InventoryHold as exc:
        raise InventoryHold(str(exc), 'ACTIVE_UNITS_PARSE',
                            systemd_dbus_state='INCOMPLETE_READONLY',
                            **context) from None
    try:
        deps = _dependencies(outputs[2])
    except InventoryHold as exc:
        raise InventoryHold(str(exc), 'TARGET_DEPS_PARSE',
                            systemd_dbus_state='INCOMPLETE_READONLY',
                            **context) from None
    risk = ('PRESENT' if 'PRESENT' in (enabled, active, timers, sockets, deps)
            else 'UNVERIFIED')
    return {**_fixed_receipt(), **metadata, 'probe_extent': 'FULL',
            'logind_status': logind_status, 'linger_effective': linger,
            'enabled_user_units': enabled, 'active_user_services': active,
            'user_timers': timers, 'user_sockets': sockets,
            'default_target_dependencies': deps, 'autostart_risk': risk,
            'systemd_dbus_state': 'QUERIED_READONLY'}


def _partial_receipt(exc):
    reason = str(exc)
    phase = exc.phase
    allowed_phases = ('PREFLIGHT', 'LOGIND_QUERY', 'UNIT_FILES_QUERY',
                      'ACTIVE_UNITS_QUERY', 'TARGET_DEPS_QUERY', 'LOGIND_PARSE',
                      'UNIT_FILES_PARSE', 'ACTIVE_UNITS_PARSE', 'TARGET_DEPS_PARSE')
    if reason not in REASON_ALLOWLIST or phase not in allowed_phases:
        reason, phase = 'INVENTORY_UNVERIFIED', 'INVENTORY_UNKNOWN'
        exc = InventoryHold(reason, phase)
    runtime_status = (exc.user_runtime_status if exc.user_runtime_status in METADATA_STATES
                      else 'UNVERIFIED')
    bus_status = (exc.user_bus_status if exc.user_bus_status in METADATA_STATES | {'NOT_CHECKED'}
                  else 'NOT_CHECKED')
    bits = (exc.bus_mode_other_bits if exc.bus_mode_other_bits in BUS_OTHER_BITS
            else 'NOT_CHECKED')
    linger = exc.linger if exc.linger in ('YES','NO','UNVERIFIED') else 'UNVERIFIED'
    logind = (exc.logind_status if exc.logind_status in LOGIND_STATES
              else 'NOT_QUERIED')
    dbus = (exc.systemd_dbus_state if exc.systemd_dbus_state in (
            'NOT_QUERIED', 'INCOMPLETE_READONLY') else 'NOT_QUERIED')
    unit_state = 'UNVERIFIED' if dbus == 'INCOMPLETE_READONLY' else 'NOT_QUERIED'
    return {**_fixed_receipt(), 'probe_extent': 'PARTIAL',
            'reason': reason, 'phase': phase,
            'user_runtime_status': runtime_status, 'user_bus_status': bus_status,
            'bus_mode_other_bits': bits, 'logind_status': logind,
            'linger_effective': linger,
            **{key: unit_state for key in UNIT_FIELDS},
            'autostart_risk': 'UNVERIFIED', 'systemd_dbus_state': dbus}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv != ['--linger-inventory']:
        return 79
    try:
        result = collect()
    except InventoryHold as exc:
        # Category-only partial receipt; never reflect raw host details.
        result = _partial_receipt(exc)
    except Exception:
        result = _partial_receipt(InventoryHold('INVENTORY_UNVERIFIED',
                                               'INVENTORY_UNKNOWN'))
    print(json.dumps(result, sort_keys=True, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
