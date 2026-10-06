"""Controlled whole-Git governed runner, derived from the preserved Source-C v3 helper.

Run once per approved frozen interpreter and whole Git worktree. There is no
receipt-only mode, test allowlist, skip mode, installer, image build or deploy.
Python socket observations cover this process only; nested native guards keep
their own instrumentation. Installed metadata does not attest wheel bytes.
"""
from __future__ import annotations

import argparse
from collections import Counter
import ctypes
import errno
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import re
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid

FIELDS = ('st_dev', 'st_ino', 'st_uid', 'st_gid', 'st_mode', 'st_nlink',
          'st_size', 'st_blocks', 'st_atime_ns', 'st_mtime_ns', 'st_ctime_ns')
STABLE_CODE_FIELDS = tuple(field for field in FIELDS if field != 'st_atime_ns')
MAX_SOURCE_FILE = 128*1024**2
MAX_SOURCE_MEMBERS = 50000


def phase_namespace(root, output, phase):
    """Give collection and execution fresh external writable namespaces."""
    require(phase in ('collection', 'execution'), 'LITERAL_GOVERNED_PHASE_REQUIRED')
    private = safe_path(output/(phase+'-private'))
    require(not private.exists() and not private.is_relative_to(root),
            'FRESH_EXTERNAL_PHASE_NAMESPACE_REQUIRED')
    private.mkdir(mode=0o700)
    paths = {name: private/name for name in ('hypothesis', 'logs')}
    for path in paths.values():
        path.mkdir(mode=0o700)
    # multiprocessing's concrete AF_UNIX listener paths have a native length
    # bound. Choose a fresh private sibling on this same filesystem, rather
    # than moving fixtures onto tmpfs or hiding a path in the source tree.
    for parent in reversed(output.parents):
        attributes = parent.lstat()
        if (stat.S_ISDIR(attributes.st_mode) and attributes.st_uid == os.geteuid()
                and attributes.st_dev == output.lstat().st_dev
                and not stat.S_IMODE(attributes.st_mode) & 0o022
                and not parent.is_relative_to(root) and len(os.fsencode(parent)) <= 50):
            paths['tmp'] = safe_path(tempfile.mkdtemp(prefix='rc6gov-', dir=parent))
            break
    require('tmp' in paths, 'SHORT_EXTERNAL_OWNED_SAME_FILESYSTEM_NAMESPACE_REQUIRED')
    tmp_identity = paths['tmp'].lstat()
    require(tmp_identity.st_uid == os.geteuid() and stat.S_IMODE(tmp_identity.st_mode) == 0o700
            and tmp_identity.st_dev == output.lstat().st_dev,
            'SHORT_TEMPORARY_NAMESPACE_CUSTODY_REQUIRED')
    os.environ.update({'TMPDIR': str(paths['tmp']), 'RUNNER_TEMP': str(private),
        'HYPOTHESIS_STORAGE_DIRECTORY': str(paths['hypothesis']), 'LOG_DIR': str(paths['logs']),
        'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'PYTHONDONTWRITEBYTECODE': '1'})
    tempfile.tempdir = str(paths['tmp'])
    sys.dont_write_bytecode = True
    return {'root': str(private), 'tmpdir': str(paths['tmp']),
            'hypothesis_storage_directory': str(paths['hypothesis']), 'log_dir': str(paths['logs']),
            'tmpdir_device': tmp_identity.st_dev, 'tmpdir_owner_uid': tmp_identity.st_uid,
            'tmpdir_retained_for_diagnostics': True,
            'pytest_cache_provider_disabled': True, 'writable_source_exclusions_added': []}


def restrict_inet_creation():
    """Make offline INET capability genuinely unavailable before optional probes.

    This process-local Linux filter denies socket/socketpair creation for
    AF_INET6. IPv4 creation grants no operation authority and remains available
    to original native adversarial guards. This is not a network namespace, a binary audit, or a
    proof about inherited descriptors or every child's networking behavior.
    No Python/dependency object is replaced. The operation audit remains
    separately responsible for a RED if bind/connect/DNS is ever reached.
    """
    architectures = {'x86_64': (0xc000003e, 41, 53), 'aarch64': (0xc00000b7, 198, 199)}
    machine = platform.machine()
    require(sys.platform == 'linux' and machine in architectures and ctypes.sizeof(ctypes.c_void_p) == 8,
            'SUPPORTED_NATIVE_LINUX_SECCOMP_ARCHITECTURE_REQUIRED')
    arch, socket_nr, socketpair_nr = architectures[machine]
    class Filter(ctypes.Structure):
        _fields_ = [('code', ctypes.c_ushort), ('jt', ctypes.c_ubyte),
                    ('jf', ctypes.c_ubyte), ('k', ctypes.c_uint32)]
    class Program(ctypes.Structure):
        _fields_ = [('length', ctypes.c_ushort), ('filters', ctypes.POINTER(Filter))]
    # Architecture match, no alternate x32 ABI, then the family argument of
    # the two native creation syscalls. All other syscalls retain their policy.
    instructions = [
        (0x20, 0, 0, 4), (0x15, 1, 0, arch), (0x06, 0, 0, 0x80000000),
        (0x20, 0, 0, 0), (0x35, 0, 1, 0x40000000), (0x06, 0, 0, 0x00050000 | errno.ENOSYS),
        (0x15, 1, 0, socket_nr), (0x15, 0, 2, socketpair_nr), (0x20, 0, 0, 16),
        (0x15, 1, 0, socket.AF_INET6),
        (0x06, 0, 0, 0x7fff0000), (0x06, 0, 0, 0x00050000 | errno.EAFNOSUPPORT),
    ]
    filters = (Filter*len(instructions))(*(Filter(*row) for row in instructions))
    program = Program(len(instructions), filters)
    libc = ctypes.CDLL(None, use_errno=True)
    require(libc.prctl(38, 1, 0, 0, 0) == 0 and libc.prctl(39, 0, 0, 0, 0) == 1,
            'OFFLINE_NO_NEW_PRIVILEGES_NOT_INSTALLED')
    require(libc.prctl(22, 2, ctypes.byref(program), 0, 0) == 0
            and libc.prctl(21, 0, 0, 0, 0) == 2, 'OFFLINE_INET_CREATION_FILTER_NOT_INSTALLED')
    witnesses = []
    for family in (socket.AF_INET6,):
        try:
            descriptor = socket.socket(family, socket.SOCK_STREAM)
        except OSError as error:
            require(error.errno == errno.EAFNOSUPPORT, 'OFFLINE_INET_CREATION_DENIAL_NOT_WITNESSED')
            witnesses.append({'family': int(family), 'operation': 'socket_creation', 'errno': error.errno,
                              'socket_created': False})
        else:
            descriptor.close()
            raise RuntimeError('OFFLINE_INET_SOCKET_CREATION_UNEXPECTEDLY_AVAILABLE')
    left, right = socket.socketpair(socket.AF_UNIX)
    try:
        left.sendall(b'R')
        require(right.recv(1) == b'R', 'OFFLINE_AF_UNIX_IPC_NOT_FUNCTIONAL')
    finally:
        left.close(); right.close()
    return {'status': 'INSTALLED_AND_KERNEL_WITNESSED', 'architecture': machine,
            'no_new_privileges': True, 'native_socket_creation_denied_families': [int(socket.AF_INET6)],
            'ipv4_creation_grants_operation_authority': False,
            'kernel_errno': errno.EAFNOSUPPORT, 'creation_denial_witnesses': witnesses,
            'af_unix_ipc_functional': True,
            'transitive_kernel_network_attestation': False,
            'scope': 'NATIVE_SOCKET_AND_SOCKETPAIR_CREATION_FAMILIES_ONLY; NOT_NAMESPACE_BINARY_OR_INHERITED_DESCRIPTOR_ATTESTATION'}


def child_infrastructure_snapshot():
    from multiprocessing import resource_tracker, forkserver
    tracker, server = resource_tracker._resource_tracker, forkserver._forkserver
    return {'resource_tracker_pid': tracker._pid, 'resource_tracker_fd': tracker._fd,
            'forkserver_pid': server._forkserver_pid, 'forkserver_alive_fd': server._forkserver_alive_fd}


def finalize_child_infrastructure(initial, limit=5):
    """Finish actual stdlib lifecycle before the parent observes main-PID exit.

    Known children are joined. Python os.kill/os.killpg nonzero signals are
    vetoed during finalization; a swallowed finalizer exception cannot clear
    its RED. This observation is not a native-binary or transitive signal
    attestation. Real stdlib finalizers and stop protocols run in one bounded
    daemon thread. A stuck lifecycle stays RED under the unchanged parent
    watchdog and owned-PGID cleanup. An inherited tracker is never reaped here.
    """
    import multiprocessing as mp
    from multiprocessing import util, resource_tracker, forkserver
    require(limit == 5, 'ORIGINAL_CHILD_FINALIZATION_BOUND_REQUIRED')
    entered = time.monotonic()
    deadline = entered+limit
    report = {'status': 'RED', 'management_bound_seconds': limit, 'forced_termination': None,
              'forced_termination_attempted': False, 'signal_vetoed': False,
              'termination_signal_attempts': [], 'signal_guard_installed_and_witnessed': False,
              'forced_termination_observation_scope':
              'PYTHON_OS_KILL_AND_OS_KILLPG_DURING_THIS_FINALIZATION_ONLY; NOT_NATIVE_BINARY_OR_TRANSITIVE_ATTESTATION',
              'initial': initial, 'joined_children': [], 'stopped_owned_infrastructure': [],
              'inherited_tracker_preserved': False, 'kernel_echild_before_phase_return': False,
              'errors': [], 'unexpected_kernel_children': []}
    signal_guard = {'active': False, 'witness_seen': False}
    witness = object()

    def audit_finalization_signal(event, values):
        if event == 'rc6.child_finalization.signal_guard' and values == (witness,):
            signal_guard['witness_seen'] = True
        if (signal_guard['active'] and event in ('os.kill', 'os.killpg')
                and values[1] != 0):
            report['termination_signal_attempts'].append({'event': event,
                'pid_or_pgid': values[0], 'signal': int(values[1]), 'vetoed_before_syscall': True})
            report['forced_termination_attempted'] = True
            report['signal_vetoed'] = True
            raise RuntimeError('CHILD_FINALIZATION_SIGNAL_DENIED_BEFORE_SYSCALL')

    # addaudithook can silently fail when an earlier hook vetoes registration.
    # Witness actual invocation before claiming the guard was installed.
    sys.addaudithook(audit_finalization_signal)
    sys.audit('rc6.child_finalization.signal_guard', witness)
    require(signal_guard['witness_seen'], 'CHILD_FINALIZATION_SIGNAL_GUARD_NOT_INSTALLED')
    report['signal_guard_installed_and_witnessed'] = True
    signal_guard['active'] = True

    def own_kernel_child(pid):
        require(type(pid) is int and pid > 0 and pid != os.getpid(), 'OWN_INFRASTRUCTURE_PID_REQUIRED')
        descriptor = os.open('/proc/'+str(pid)+'/status', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            raw = os.read(descriptor, 65537)
            require(len(raw) <= 65536, 'INFRASTRUCTURE_KERNEL_IDENTITY_BOUND')
        finally:
            os.close(descriptor)
        identity = {line.partition(':')[0]: line.partition(':')[2].strip()
                    for line in raw.decode().splitlines() if line.startswith(('Pid:', 'PPid:', 'Uid:'))}
        require(identity.get('Pid') == str(pid) and identity.get('PPid') == str(os.getpid())
                and identity.get('Uid', '').split()[:2] == [str(os.getuid()), str(os.geteuid())],
                'INFRASTRUCTURE_NOT_AN_OWN_KERNEL_CHILD')
        return identity

    def finalize():
        try:
            util._run_finalizers(0)
            require(not report['termination_signal_attempts'], 'CHILD_FINALIZATION_SIGNAL_ATTEMPT_DENIED')
            for process in mp.active_children():
                process.join(max(0, deadline-time.monotonic()))
                require(not process.is_alive(), 'OWN_MULTIPROCESSING_CHILD_DID_NOT_FINISH')
                report['joined_children'].append({'pid': process.pid, 'exitcode': process.exitcode})
            server, tracker = forkserver._forkserver, resource_tracker._resource_tracker
            if server._forkserver_pid is not None:
                pid = server._forkserver_pid
                identity = own_kernel_child(pid)
                server._stop()  # Actual alive-pipe close, specific waitpid and socket removal.
                require(server._forkserver_pid is None and server._forkserver_alive_fd is None,
                        'OWN_FORKSERVER_NOT_STOPPED')
                report['stopped_owned_infrastructure'].append({'kind': 'forkserver', 'pid': pid, 'kernel_identity': identity})
            # Keep the concrete forkserver socket's owning temporary directory
            # until its real stop/unlink protocol has completed.
            util._run_finalizers()
            require(not report['termination_signal_attempts'], 'CHILD_FINALIZATION_SIGNAL_ATTEMPT_DENIED')
            if tracker._pid is not None:
                pid = tracker._pid
                identity = own_kernel_child(pid)
                tracker._stop()  # Actual tracker alive-pipe close and specific waitpid.
                require(tracker._pid is None and tracker._fd is None, 'OWN_RESOURCE_TRACKER_NOT_STOPPED')
                report['stopped_owned_infrastructure'].append({'kind': 'resource_tracker', 'pid': pid, 'kernel_identity': identity})
            elif tracker._fd is not None:
                require(initial['resource_tracker_pid'] is None
                        and initial['resource_tracker_fd'] == tracker._fd,
                        'UNEXPECTED_INHERITED_RESOURCE_TRACKER_STATE')
                report['inherited_tracker_preserved'] = True
            try:
                found, status, _usage = os.wait4(-1, os.WNOHANG)
            except ChildProcessError as error:
                require(error.errno == errno.ECHILD, 'CHILD_KERNEL_EXHAUSTION_ERRNO_UNVERIFIED')
                report['kernel_echild_before_phase_return'] = True
            else:
                report['unexpected_kernel_children'].append({'pid': found,
                    'exitcode': os.waitstatus_to_exitcode(status) if found else None})
                raise RuntimeError('CHILD_KERNEL_EXHAUSTION_NOT_PROVED')
            require(time.monotonic() <= deadline, 'CHILD_FINALIZATION_DEADLINE_EXCEEDED')
            require(not report['termination_signal_attempts'], 'CHILD_FINALIZATION_SIGNAL_ATTEMPT_DENIED')
            report['status'] = 'GREEN'
        except BaseException as error:
            report['errors'].append({'class': type(error).__name__, 'reason': str(error)})
        finally:
            signal_guard['active'] = False

    thread = threading.Thread(target=finalize, name='rc6-owned-child-finalization', daemon=True)
    thread.start()
    thread.join(max(0, deadline-time.monotonic()))
    if thread.is_alive():
        report['status'] = 'RED'
        report['errors'].append({'class': 'TimeoutError', 'reason': 'CHILD_FINALIZATION_DID_NOT_RETURN_WITHIN_BOUND'})
    report['wall_seconds'] = time.monotonic()-entered
    report['finalization_thread_finished'] = not thread.is_alive()
    if report['finalization_thread_finished']:
        # Every nonzero signal in the declared Python observation window is
        # vetoed before syscall. This says nothing about unobserved native code.
        report['forced_termination'] = False
    if report['termination_signal_attempts']:
        report['status'] = 'RED'
        if not any(error['reason'] == 'CHILD_FINALIZATION_SIGNAL_ATTEMPT_DENIED' for error in report['errors']):
            report['errors'].append({'class': 'RuntimeError', 'reason': 'CHILD_FINALIZATION_SIGNAL_ATTEMPT_DENIED'})
    return report


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False)+'\n').encode()


def safe_path(value):
    path = Path(os.path.abspath(value))
    for parent in [path, *path.parents]:
        require(not parent.is_symlink(), 'PATH_ALIAS_FORBIDDEN')
    return path


def capture(path, limit=MAX_SOURCE_FILE):
    path = safe_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                and before.st_uid == os.geteuid() and before.st_size <= limit,
                'REGULAR_PRIVATE_OWNED_FILE_REQUIRED')
        parts, size = [], 0
        while True:
            part = os.read(fd, min(1024**2, limit+1-size))
            if not part:
                break
            parts.append(part); size += len(part)
            require(size <= limit, 'FILE_BOUND_EXCEEDED')
        after, location = os.fstat(fd), path.lstat()
        require(size == before.st_size and all(getattr(before, key) == getattr(after, key)
                == getattr(location, key) for key in FIELDS), 'FILE_CHANGED_DURING_CAPTURE')
        return b''.join(parts), {key: getattr(before, key) for key in FIELDS}
    finally:
        os.close(fd)


def publish(path, data):
    fd = os.open(safe_path(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data); stream.flush(); os.fchmod(stream.fileno(), 0o644); os.fsync(stream.fileno())


def git(root, *arguments):
    return subprocess.check_output(['git', '--no-replace-objects', '-c', 'protocol.allow=never',
        '-C', str(root), *arguments], env={**os.environ, 'GIT_NO_LAZY_FETCH': '1'},
        stderr=subprocess.PIPE, timeout=30)


def git_metadata(root):
    """Validate the only omitted physical member against Git's actual metadata."""
    marker = root/'.git'
    marker_stat = marker.lstat()
    require(marker_stat.st_uid == os.geteuid() and not marker.is_symlink(),
            'GIT_METADATA_CUSTODY_REQUIRED')
    directory = safe_path(git(root, 'rev-parse', '--absolute-git-dir').decode().strip())
    directory_stat = directory.lstat()
    require(stat.S_ISDIR(directory_stat.st_mode) and directory_stat.st_uid == os.geteuid(),
            'REAL_GIT_METADATA_DIRECTORY_REQUIRED')
    if stat.S_ISDIR(marker_stat.st_mode):
        require(directory == marker and (marker_stat.st_dev, marker_stat.st_ino)
                == (directory_stat.st_dev, directory_stat.st_ino), 'GIT_DIRECTORY_BINDING_MISMATCH')
    else:
        raw, attributes = capture(marker, limit=4096)
        require(raw.startswith(b'gitdir: ') and raw.endswith(b'\n') and raw.count(b'\n') == 1
                and b'\0' not in raw and b'\r' not in raw, 'GIT_WORKTREE_POINTER_INVALID')
        pointer = os.fsdecode(raw[8:-1])
        require(pointer and safe_path(pointer if os.path.isabs(pointer) else root/pointer) == directory
                and attributes['st_nlink'] == 1, 'GIT_WORKTREE_METADATA_BINDING_MISMATCH')
    return {'omitted_path': '.git', 'verified_git_directory': str(directory),
            'scope': 'ONLY_REAL_ROOT_GIT_METADATA; NO_TEST_OUTPUT_VENV_CACHE_OR_IGNORED_FILE_EXCLUSION'}


def physical_namespace(root):
    """Enumerate all physical source members without following paths or aliases."""
    directories, files, seen = {}, {}, set()
    root_identity = root.lstat()
    require(stat.S_ISDIR(root_identity.st_mode) and root_identity.st_uid == os.geteuid()
            and not stat.S_IMODE(root_identity.st_mode) & 0o022, 'PRIVATE_OWNED_SOURCE_ROOT_REQUIRED')
    def member_names(descriptor):
        values = []
        with os.scandir(descriptor) as listing:
            for entry in listing:
                values.append(entry.name)
                require(len(values)+len(files)+len(directories) <= 2*MAX_SOURCE_MEMBERS,
                        'PHYSICAL_SOURCE_NAMESPACE_BOUND')
        return sorted(values)
    def visit(relative, descriptor):
        before = os.fstat(descriptor)
        identity = (before.st_dev, before.st_ino)
        require(stat.S_ISDIR(before.st_mode) and before.st_uid == os.geteuid()
                and before.st_dev == root_identity.st_dev and identity not in seen
                and not stat.S_IMODE(before.st_mode) & 0o022, 'SOURCE_DIRECTORY_ALIAS_CUSTODY_OR_MOUNT_FORBIDDEN')
        seen.add(identity)
        directories[relative or '.'] = {key: getattr(before, key) for key in FIELDS}
        names = member_names(descriptor)
        for name in names:
            require(name not in ('.', '..') and '/' not in name and '\\' not in name
                    and not any(ord(char) < 32 for char in name), 'PHYSICAL_SOURCE_NAME_INVALID')
            if not relative and name == '.git':
                continue  # Caller validates this actual metadata, never a filename-only exemption.
            location = relative+'/'+name if relative else name
            attributes = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(attributes.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME,
                                dir_fd=descriptor)
                try:
                    opened = os.fstat(child)
                    require(all(getattr(attributes, key) == getattr(opened, key) for key in FIELDS),
                            'SOURCE_DIRECTORY_CHANGED_BEFORE_OPEN')
                    visit(location, child)
                finally:
                    os.close(child)
            else:
                require(stat.S_ISREG(attributes.st_mode) and attributes.st_nlink == 1
                        and attributes.st_uid == os.geteuid() and attributes.st_dev == root_identity.st_dev,
                        'PHYSICAL_SOURCE_ALIAS_OR_NONREGULAR_OR_CUSTODY_FORBIDDEN:'+location)
                files[location] = {key: getattr(attributes, key) for key in FIELDS}
            current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            require(all(getattr(attributes, key) == getattr(current, key) for key in FIELDS),
                    'PHYSICAL_SOURCE_MEMBER_CHANGED_DURING_ENUMERATION')
        after = os.fstat(descriptor)
        require(names == member_names(descriptor)
                and all(getattr(before, key) == getattr(after, key) for key in FIELDS),
                'PHYSICAL_SOURCE_DIRECTORY_CHANGED_DURING_ENUMERATION')
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME)
    try:
        visit('', descriptor)
        require(all(getattr(root_identity, key) == getattr(os.fstat(descriptor), key)
                    == getattr(root.lstat(), key) for key in FIELDS), 'SOURCE_ROOT_IDENTITY_CHANGED')
    finally:
        os.close(descriptor)
    return {'files': files, 'directories': directories}


def source_pin(root, sha, tree):
    require(root.is_dir() and (root/'.git').exists() and not (root/'.git').is_symlink(), 'WHOLE_GIT_WORKTREE_REQUIRED')
    require(not any(key in os.environ for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE',
            'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES')), 'GIT_AUTHORITY_ENV_OVERRIDE_FORBIDDEN')
    require(os.environ.get('GIT_REPLACE_REF_BASE', 'refs/replace/') == 'refs/replace/'
            and not git(root, 'for-each-ref', '--format=%(refname)', 'refs/replace/').strip(),
            'GIT_REPLACEMENTS_FORBIDDEN')
    require(git(root, 'rev-parse', 'HEAD').decode().strip() == sha
            and git(root, 'rev-parse', 'HEAD^{tree}').decode().strip() == tree,
            'LITERAL_CANDIDATE_SHA_TREE_MISMATCH')
    raw_commit = git(root, 'cat-file', 'commit', sha)
    require(hashlib.sha1(b'commit '+str(len(raw_commit)).encode()+b'\0'+raw_commit).hexdigest() == sha
            and raw_commit.splitlines()[0] == b'tree '+tree.encode(), 'RAW_GIT_COMMIT_IDENTITY_MISMATCH')
    require(not git(root, 'status', '--porcelain', '--untracked-files=no').strip(), 'TRACKED_SOURCE_CHANGED')
    metadata_binding = git_metadata(root)
    records = {}
    entries = git(root, 'ls-tree', '-r', '-z', sha).split(b'\0')
    physical_before = physical_namespace(root)
    for entry in entries:
        if not entry:
            continue
        header, encoded_name = entry.split(b'\t', 1)
        mode, kind, blob = header.decode().split(); name = os.fsdecode(encoded_name)
        require(kind == 'blob' and mode in ('100644', '100755') and name not in records
                and Path(name).as_posix() == name and not Path(name).is_absolute()
                and '..' not in Path(name).parts and '\\' not in name, 'UNSUPPORTED_LITERAL_TREE_ENTRY')
        data, attributes = capture(root/name)
        require(stat.S_IMODE(attributes['st_mode']) == (0o755 if mode == '100755' else 0o644)
                and hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest() == blob,
                'TRACKED_BYTES_MODES_BLOB_MISMATCH:'+name)
        records[name] = {'bytes': len(data), 'sha256': digest(data), 'git_blob': blob,
                         'git_mode': mode, 'stat_fields': attributes}
    require(0 < len(records) <= MAX_SOURCE_MEMBERS, 'WHOLE_TREE_FILE_BOUND')
    expected_directories = {'.'}
    for name in records:
        expected_directories.update(parent.as_posix() for parent in Path(name).parents if parent != Path('.'))
    physical_after = physical_namespace(root)
    require(physical_before == physical_after, 'SOURCE_NAMESPACE_CHANGED_DURING_PIN_CAPTURE')
    require(set(physical_after['files']) == set(records)
            and set(physical_after['directories']) == expected_directories,
            'PHYSICAL_SOURCE_EXTRA_MISSING_IGNORED_OR_EMPTY_DIRECTORY_FORBIDDEN')
    for name, row in records.items():
        require(row['stat_fields'] == physical_after['files'][name], 'SOURCE_STAT_PIN_NAMESPACE_MISMATCH')
    require(git_metadata(root) == metadata_binding, 'GIT_METADATA_BINDING_CHANGED')
    return {'source_sha': sha, 'source_tree': tree, 'raw_commit_sha256': digest(raw_commit),
            'files': records, 'overlay_count': 0,
            'physical_directories': physical_after['directories'], 'git_metadata': metadata_binding,
            'physical_namespace_exact_to_literal_tree': True,
            'scope': 'ALL_LITERAL_HEAD_GIT_BLOBS_AND_EXACT_PHYSICAL_NAMESPACE_MODES_AND_OWNED_FILE_DIRECTORY_CUSTODY'}


def compare_source(before, after):
    require(set(before['files']) == set(after['files'])
            and set(before['physical_directories']) == set(after['physical_directories'])
            and before['git_metadata'] == after['git_metadata'], 'SOURCE_NAMESPACE_OR_GIT_METADATA_BINDING_CHANGED')
    atime = []
    for name, row in before['files'].items():
        other = after['files'][name]
        require(all(row[key] == other[key] for key in ('bytes', 'sha256', 'git_blob', 'git_mode'))
                and all(row['stat_fields'][key] == other['stat_fields'][key] for key in STABLE_CODE_FIELDS),
                'SOURCE_BYTES_MODE_BLOB_OR_STABLE_CUSTODY_CHANGED:'+name)
        if row['stat_fields']['st_atime_ns'] != other['stat_fields']['st_atime_ns']:
            atime.append({'path': name, 'kind': 'file', 'before': row['stat_fields']['st_atime_ns'],
                          'after': other['stat_fields']['st_atime_ns']})
    for name, row in before['physical_directories'].items():
        other = after['physical_directories'][name]
        require(all(row[key] == other[key] for key in STABLE_CODE_FIELDS),
                'SOURCE_DIRECTORY_STABLE_CUSTODY_CHANGED:'+name)
        if row['st_atime_ns'] != other['st_atime_ns']:
            atime.append({'path': name, 'kind': 'directory', 'before': row['st_atime_ns'], 'after': other['st_atime_ns']})
    return atime


def installed_closure(root):
    policy = json.loads(capture(root/'ops/policy/rc6-supply-chain-v1.json')[0])
    normalize = lambda name: re.sub(r'[-_.]+', '-', name.lower())
    expected = {normalize(row['name']): row['version'] for row in policy['packages']+policy['build_tools']}
    rows = [(normalize(row.metadata['Name']), row.version) for row in metadata.distributions()]
    observed = dict(rows)
    duplicates = sorted(name for name, count in Counter(name for name, _ in rows).items() if count != 1)
    require(len(expected) == len(rows) == len(observed) == 157 and not duplicates
            and expected == observed, 'EXACT157_APPROVED_INSTALLED_NAMES_VERSIONS_REQUIRED')
    sys.path.insert(0, str(root))
    from scripts.porota_dependency_repro_audit import audit, current_platform_identity
    result = audit(capture(root/'requirements.txt')[0].decode(),
        capture(root/'requirements.lock.txt')[0].decode(), capture(root/'Dockerfile')[0].decode(),
        supply_chain_policy=policy, build_lock_text=capture(root/'requirements.build.lock.txt')[0].decode(),
        workflow_texts={name: capture(root/name)[0].decode() for name in
            ('.github/workflows/porota-predeploy-v2.yml', '.github/workflows/porota-deploy-v2-promote.yml')},
        current_platform=current_platform_identity())
    require(result['status'] == 'GREEN', 'HASH_LOCK_PLATFORM_OR_CANONICAL_ACTION_POLICY_GAP')
    return {'schema': 'rc6.frozen-governed-installed-preflight.v1', 'status': 'GREEN',
            'expected_total': 157, 'installed_total': len(rows), 'duplicate_names': duplicates,
            'metadata_names_versions_exact': True, 'installed_distribution_bytes_authenticated': False,
            'installer_executed_by_this_runner': False, 'source_hash_lock_action_platform_audit': result}


def approved_scope(root, sha):
    import yaml
    from scripts import rc6_convergence_provenance as verifier
    inventory = verifier.verify(root, sha, fetch_source_refs=False)
    policy = yaml.safe_load(capture(root/'ops/policy/test-policy.yaml')[0])
    scope, governance = policy['productive_scope'], policy['test_governance']
    require(scope['roots'] == ['.'] and governance['automatic_discovery_required'] is True
            and governance['manual_test_file_allowlist_as_primary_ci'] is False, 'ROOT_AUTOMATIC_SCOPE_REQUIRED')
    manifest_raw = verifier.committed_input(root, sha, verifier.MANIFEST)
    require(digest(manifest_raw) == verifier.ORIGINAL_DIGESTS[verifier.MANIFEST], 'ORIGINAL_AUTHORITY_MANIFEST_CHANGED')
    manifest = verifier.read_json(manifest_raw)
    original = next(row for row in manifest['sources'] if row['pr'] == 466)
    original_paths = manifest['expected_source_paths']['466']
    matrix = next(row for row in original_paths if row['path'] == verifier.PRIOR_AUDIT_MATRIX)
    raw = git(root, 'cat-file', 'blob', matrix['blob'])
    require(git(root, 'rev-parse', original['head_sha']+'^{tree}').decode().strip() == original['tree']
            and hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest() == matrix['blob'],
            'ORIGINAL_MATRIX_GIT_AUTHORITY_MISMATCH')
    expected = verifier.read_json(raw)['governed_exclusions']
    require(type(expected) is list and len(expected) == 1, 'ORIGINAL_UNIQUE_A3_EXCLUSION_REQUIRED')
    arguments, exclusions = ['.'], []
    for row in scope['exclusions']:
        require(all(row.get(key) for key in ('classification', 'reason', 'owner')), 'EXCLUSION_CLASSIFICATION_REQUIRED')
        require((root/row['path']).is_file() and (root/row['successor']).is_file(), 'EXCLUSION_SUCCESSOR_REQUIRED')
        arguments.append('--ignore='+row['path'])
        exclusions.append('|'.join((row['path'], row['classification'], row['successor'])))
    require(exclusions == expected, 'ORIGINAL_A3_POLICY_EXCLUSION_DRIFT')
    return arguments, exclusions, inventory


def subprocess_phase(command, root, output, env, limit):
    """Require exclusive kernel child custody before accepting a pytest phase.

    The PID-specific wait4 witness and its adopted-child drain are separate.
    Missing proc children files are never used as exhaustion evidence. Only
    ECHILD after the actual main PID reap proves this supervisor is exhausted.
    Signals address only the PGID created by this phase's Popen; once absence
    is observed, this invocation never sends another signal to that PGID.
    """
    phase_entered = time.monotonic()
    started, interrupted, cleanup_started = None, None, None
    process, waited, usage, reap_wall = None, 0, None, None
    active, pre_spawn_echild, exhausted = False, False, False
    timed_out, late_reap, wait4_zero = False, False, False
    group_absent, group_absent_at_main_reap = False, False
    pending, residual, descendants, errors, observations, signals = [], [], [], [], [], []
    supervisor_pid, supervisor_tid = os.getpid(), threading.get_native_id()
    task_root = '/proc/' + str(supervisor_pid) + '/task/' + str(supervisor_tid)
    identity = {}
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()

    def error_record(label, error):
        errors.append({'phase': label, 'class': type(error).__name__, 'reason': str(error)})

    def observe_group_absence():
        nonlocal group_absent
        if process is None or group_absent:
            return group_absent
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            group_absent = True
        return group_absent

    def signal_group(number):
        nonlocal group_absent
        if process is None or group_absent or observe_group_absence():
            return
        try:
            os.killpg(process.pid, number)
        except ProcessLookupError:
            group_absent = True
            signals.append({'pgid': process.pid, 'signal': number, 'outcome': 'ESRCH'})
        else:
            signals.append({'pgid': process.pid, 'signal': number, 'outcome': 'SENT'})

    def reap_main():
        nonlocal waited, usage, reap_wall, late_reap, timed_out
        if waited:
            return waited
        found, child_status, child_usage = os.wait4(process.pid, os.WNOHANG)
        if found:
            require(found == process.pid, 'SPECIFIC_PHASE_PID_WAIT4_IDENTITY_MISMATCH')
            waited, usage = found, child_usage
            reap_wall = time.monotonic() - started
            process.returncode = os.waitstatus_to_exitcode(child_status)
            late_reap = reap_wall > limit
            timed_out = timed_out or late_reap
        return found

    def child_row(found, child_status, child_usage):
        return {'pid': found, 'exit_code': os.waitstatus_to_exitcode(child_status),
                'cpu_user_seconds': child_usage.ru_utime,
                'cpu_system_seconds': child_usage.ru_stime,
                'kernel_lifetime_peak_rss_bytes': child_usage.ru_maxrss * 1024}

    try:
        # This is the supervisor's own kernel status only, never a global /proc
        # scan or a dependency on the kernel's optional task children facility.
        descriptor = os.open(task_root + '/status', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            status_data = os.read(descriptor, 65537)
            require(len(status_data) <= 65536, 'SUPERVISOR_KERNEL_STATUS_BOUND_EXCEEDED')
        finally:
            os.close(descriptor)
        identity = {line.partition(':')[0]: line.partition(':')[2].strip()
                    for line in status_data.decode().splitlines()
                    if line.startswith(('Pid:', 'Tgid:', 'Uid:'))}
        require(identity.get('Pid') == str(supervisor_tid)
                and identity.get('Tgid') == str(supervisor_pid)
                and identity.get('Uid', '').split()[:2] == [str(os.getuid()), str(os.geteuid())],
                'EXPLICIT_SUPERVISOR_KERNEL_PID_TID_UID_BINDING_REQUIRED')
        require(libc.prctl(37, ctypes.byref(previous), 0, 0, 0) == 0,
                'LINUX_OWN_SUBREAPER_INITIAL_STATE_REQUIRED')
        require(libc.prctl(36, 1, 0, 0, 0) == 0, 'LINUX_OWN_CHILD_SUBREAPER_REQUIRED')
        active = True
        try:
            pre_pid, pre_status, pre_usage = os.wait4(-1, os.WNOHANG)
        except ChildProcessError as error:
            if error.errno != errno.ECHILD:
                raise
            pre_spawn_echild = True
            observations.append({'phase': 'before_popen_after_subreaper_activation',
                                 'outcome': 'ECHILD', 'errno': error.errno})
        else:
            observations.append({'phase': 'before_popen_after_subreaper_activation',
                                 'outcome': 'PID_REAPED' if pre_pid else 'ACTIVE_OWN_CHILD_WITHOUT_PID',
                                 **(child_row(pre_pid, pre_status, pre_usage) if pre_pid else {'pid': 0})})
            raise RuntimeError('EXCLUSIVE_SUPERVISOR_REQUIRES_PRESPAWN_KERNEL_ECHILD')
        with output.open('xb') as stream:
            started = time.monotonic()
            process = subprocess.Popen(command, cwd=root, env=env, stdout=stream,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            while not reap_main():
                now = time.monotonic()
                if now - started >= limit and interrupted is None:
                    timed_out, interrupted = True, now
                    signal_group(signal.SIGINT)
                if interrupted is not None and now - interrupted >= 20:
                    signal_group(signal.SIGKILL)
                    break  # The finally below uses bounded WNOHANG; never wait4(..., 0).
                time.sleep(.1)
    except BaseException as error:
        error_record('spawn_tracking_or_log', error)
    finally:
        # This scope covers activation, Popen, logs, signal races and errors.
        # Five seconds is cleanup management after an already failed phase,
        # not an extension of the phase's acceptance deadline.
        cleanup_started = time.monotonic()
        cleanup_deadline = cleanup_started + 5
        try:
            if process is not None and not waited:
                signal_group(signal.SIGTERM)
                while time.monotonic() < cleanup_deadline and not reap_main():
                    if time.monotonic() - cleanup_started >= 1:
                        signal_group(signal.SIGKILL)
                    time.sleep(.05)
                if not waited:
                    signal_group(signal.SIGKILL)
                    reap_main()  # One final nonblocking observation, no invented reap.
                if not waited:
                    error_record('main_reap', RuntimeError('OWN_PHASE_PID_NOT_REAPED_WITHIN_CLEANUP_BOUND'))
        except BaseException as error:
            error_record('main_termination_or_reap', error)
        try:
            if process is not None and waited == process.pid:
                group_absent_at_main_reap = observe_group_absence()
        except BaseException as error:
            error_record('owned_group_after_main_reap', error)
        try:
            if active:
                require(process is not None and waited == process.pid and pre_spawn_echild,
                        'KERNEL_DRAIN_REQUIRES_REAPED_MAIN_PID_AND_EXCLUSIVE_SUBREAPER')
                while True:
                    if len(observations) >= 1024 or time.monotonic() >= cleanup_deadline:
                        error_record('kernel_owned_child_cleanup',
                                     RuntimeError('OWN_CHILD_EXHAUSTION_NOT_PROVED_WITHIN_BOUND'))
                        break
                    try:
                        found, child_status, child_usage = os.wait4(-1, os.WNOHANG)
                    except ChildProcessError as error:
                        if error.errno != errno.ECHILD:
                            raise
                        observations.append({'phase': 'after_main_pid_reap',
                                             'outcome': 'ECHILD', 'errno': error.errno})
                        exhausted, pending = True, []
                        break
                    if found:
                        require(found != process.pid, 'MAIN_PID_CANNOT_BE_REAPED_AS_AN_ADOPTED_CHILD')
                        row = child_row(found, child_status, child_usage)
                        descendants.append(row)
                        observations.append({'phase': 'after_main_pid_reap', 'outcome': 'PID_REAPED', **row})
                    else:
                        pending = ['KERNEL_ACTIVE_OWN_CHILD_WITHOUT_PID']
                        if not wait4_zero:
                            residual.append('KERNEL_WAIT4_ZERO_OBSERVED')
                            observations.append({'phase': 'after_main_pid_reap',
                                                 'outcome': 'ACTIVE_OWN_CHILD_WITHOUT_PID', 'pid': 0})
                        wait4_zero = True  # Irreversible RED even if cleanup later reaches ECHILD.
                        signal_group(signal.SIGKILL if time.monotonic() - cleanup_started >= 1 else signal.SIGTERM)
                    time.sleep(.05)
        except BaseException as error:
            error_record('kernel_owned_child_cleanup', error)
        finally:
            try:
                if process is not None:
                    observe_group_absence()
            except BaseException as error:
                error_record('owned_group_final_observation', error)
            if active:
                try:
                    require(libc.prctl(36, previous.value, 0, 0, 0) == 0,
                            'OWN_SUBREAPER_STATE_NOT_RESTORED')
                except BaseException as error:
                    error_record('subreaper_restore', error)
    return {'pid': process.pid if process is not None else None,
            'returncode': process.returncode if process is not None else None,
            'timed_out': timed_out, 'wall_seconds': time.monotonic() - phase_entered,
            'cpu_user_seconds': usage.ru_utime if usage is not None else None,
            'cpu_system_seconds': usage.ru_stime if usage is not None else None,
            'peak_rss_bytes': usage.ru_maxrss * 1024 if usage is not None else None,
            'scope': 'EXACT_PYTEST_PHASE_PID_AND_KERNEL_ACCOUNTED_REAPED_DESCENDANTS; NOT SUM_OF_CONCURRENT_RSS',
            'command': command, 'supervisor_pid': supervisor_pid, 'supervisor_native_tid': supervisor_tid,
            'supervisor_kernel_identity_before_spawn': identity,
            'proc_task_children_examined': False,
            'owned_child_exhaustion_method': 'EXCLUSIVE_SUBREAPER_PRESPAWN_ECHILD_AND_POST_MAIN_WAIT4_WNOHANG_ECHILD',
            'kernel_pre_popen_echild_verified': pre_spawn_echild,
            'wait4_reaped_pid': waited, 'actual_child_reaped': process is not None and waited == process.pid,
            'actual_launch_to_pid_reap_wall_seconds': reap_wall,
            'late_observed_main_reap_irreversible_red': late_reap,
            'phase_acceptance_deadline_seconds': limit, 'sigint_failure_grace_seconds': 20,
            'owned_cleanup_management_bound_seconds': 5,
            'process_group_absent_at_main_reap': group_absent_at_main_reap,
            'process_group_absent_after_reap': group_absent,
            'owned_children_exhaustion_verified': exhausted,
            'kernel_wait4_zero_observed_irreversible_red': wait4_zero,
            'kernel_owned_child_observations': observations, 'owned_group_signal_observations': signals,
            'residual_descendants_observed': residual, 'adopted_descendants_reaped': descendants,
            'remaining_owned_children': pending, 'supervisor_errors': errors,
            'termination_reap_restore_cleanup_seconds': time.monotonic() - cleanup_started,
            'subreaper_restore_attempted_on_all_activated_paths': active,
            'subreaper_previous_state': previous.value if active else None}


def install_phase_audit(observations):
    """Keep every reached DNS/INET operation DENIED and counted as RED."""
    def observe(event, arguments):
        if event == 'socket.__new__' and arguments[1] in (socket.AF_INET, socket.AF_INET6):
            observations['inet_socket_constructor_requests'].append({
                'event': event, 'family': int(arguments[1]),
                'capability_policy': ('NATIVE_IPV6_CREATION_DENIED_EAFNOSUPPORT; SUPPLIED_DESCRIPTORS_NOT_ATTESTED'
                    if arguments[1] == socket.AF_INET6 else 'IPV4_CREATION_ONLY_NO_OPERATION_AUTHORITY'),
                'observation_scope': 'PYTHON_CONSTRUCTOR_FAMILY_REQUEST_NOT_INDIVIDUAL_KERNEL_RESULT'})
        if event in ('socket.getaddrinfo', 'socket.gethostbyname', 'socket.gethostbyaddr'):
            observations['inet_socket_attempts'].append(event)
            raise RuntimeError('GOVERNED_DNS_OPERATION_FORBIDDEN')
        if event in ('socket.connect', 'socket.bind', 'socket.sendto', 'socket.sendmsg'):
            if getattr(arguments[0], 'family', None) in (socket.AF_INET, socket.AF_INET6):
                observations['inet_socket_attempts'].append(event)
                raise RuntimeError('GOVERNED_INET_SOCKET_OPERATION_FORBIDDEN')
        if event == 'subprocess.Popen':
            name = os.path.basename(os.fsdecode(arguments[0]))
            counts = observations['subprocess_executable_counts']; counts[name] = counts.get(name, 0)+1
            # Do not serialize argv/env: they can contain signed URLs or secrets.
            if name in ('curl', 'wget', 'ssh', 'scp', 'sftp', 'nc', 'ncat', 'socat', 'telnet'):
                observations['inet_socket_attempts'].append('NETWORK_CLIENT_EXECUTABLE_FORBIDDEN')
                raise RuntimeError('GOVERNED_NETWORK_CLIENT_EXECUTABLE_FORBIDDEN')
    sys.addaudithook(observe)


def phase_child(args):
    root, output = safe_path(args.repo_root), safe_path(args.output_root)
    namespace = phase_namespace(root, output, args.phase)
    publish(output/(args.phase+'.namespace.json'), canonical(namespace))
    capability = restrict_inet_creation()
    publish(output/(args.phase+'.offline-capability.json'), canonical(capability))
    infrastructure_initial = child_infrastructure_snapshot()
    require(all(value is None for value in infrastructure_initial.values()),
            'FRESH_PHASE_MULTIPROCESSING_INFRASTRUCTURE_REQUIRED')
    pin = source_pin(root, args.source_sha, args.source_tree)
    closure = installed_closure(root)
    runner_raw, _ = capture(__file__)
    launcher = json.loads(capture(output/'launch.json')[0])
    require(launcher['runner_sha256'] == digest(runner_raw), 'CHILD_RUNNER_CHANGED')
    test_args, exclusions, _inventory = approved_scope(root, args.source_sha)
    observations = {'inet_socket_attempts': [], 'subprocess_executable_counts': {}, 'actual_product_imports': [],
                    'unexpected_product_imports': [], 'source_files': len(pin['files']),
                    'inet_socket_constructor_requests': []}
    install_phase_audit(observations)
    import pytest
    from _pytest.junitxml import mangle_test_address
    items = []
    class Observer:
        def pytest_collection_finish(self, session):
            for item in session.items:
                address = mangle_test_address(item.nodeid)
                items.append({'nodeid': item.nodeid, 'classname': '.'.join(address[:-1]), 'name': address[-1]})
    argv = ['-q', '-p', 'no:cacheprovider', '-o', 'pythonpath=.', '-o', 'junit_family=legacy',
            '--basetemp='+str(output/(args.phase+'-private')/'pytest'), *test_args]
    if args.phase == 'collection':
        argv.append('--collect-only')
    else:
        argv.extend(['--junitxml='+str(output/'porota-governed-tests.xml')])
    try:
        rc = int(pytest.main(argv, plugins=[Observer()]))
    finally:
        finalization = finalize_child_infrastructure(infrastructure_initial)
        publish(output/(args.phase+'.child-finalization.json'), canonical(finalization))
    product_names = {Path(name).stem for name in pin['files'] if '/' not in name and name.endswith('.py')}
    product_names |= {name.split('/')[0] for name in pin['files']
                     if '/' in name and name.endswith('.py') and name.split('/')[0] not in ('tests', 'docs', '.github', '.agents')}
    for name, module in list(sys.modules.items()):
        if name.split('.')[0] not in product_names:
            continue
        origin = getattr(module, '__file__', None)
        if origin is None:
            continue  # Namespace containers have no executable member bytes.
        location = safe_path(origin)
        if not location.is_relative_to(root):
            observations['unexpected_product_imports'].append(name); continue
        relative = location.relative_to(root).as_posix()
        expected = pin['files'].get(relative)
        data, _ = capture(location)
        require(expected is not None and digest(data) == expected['sha256'], 'PRODUCT_IMPORT_SOURCE_BYTES_MISMATCH')
        observations['actual_product_imports'].append({'module': name, 'path': relative, 'sha256': digest(data)})
    after = source_pin(root, args.source_sha, args.source_tree)
    atime = compare_source(pin, after)
    publish(output/(args.phase+'.source-before.index.json'), canonical(pin))
    publish(output/(args.phase+'.source-after.index.json'), canonical(after))
    report = {'schema': 'rc6.governed-frozen-phase-observations.v1', 'phase': args.phase, 'pid': os.getpid(),
        'source_sha': args.source_sha, 'source_tree': args.source_tree, 'execution_id': launcher['execution_id'],
        'closure_before_fixture': closure, 'items': items, 'pytest_exit_code': rc, 'exclusions': exclusions,
        'source_namespace_exact_before_after': True, 'source_overlay_count': 0,
        'source_before_index_sha256': digest(canonical(pin)), 'source_after_index_sha256': digest(canonical(after)),
        'observed_code_atime_changes': atime,
        'phase_namespace': namespace, 'offline_inet_creation_capability': capability,
        'child_infrastructure_finalization': finalization,
        **observations, 'inet_observation_scope': 'THIS_PYTEST_PYTHON_PROCESS_DNS_INET_SOCKET_AUDIT_AND_KNOWN_CLIENT_EXECUTABLES; NOT A KERNEL_NETWORK_NAMESPACE_OR TRANSITIVE_CHILD_NETWORK_ATTESTATION',
        'import_observation_scope': 'ACTUAL_PRODUCT_MODULES_PRESENT_AT_PHASE_EXIT; NESTED_NATIVE_GUARDS_BIND_THEIR_OWN_SOURCE'}
    publish(output/(args.phase+'.observations.json'), canonical(report))
    require(finalization['status'] == 'GREEN' and finalization['finalization_thread_finished'] is True
            and finalization['kernel_echild_before_phase_return'] is True
            and finalization['signal_guard_installed_and_witnessed'] is True
            and not finalization['termination_signal_attempts']
            and finalization['forced_termination_attempted'] is False and finalization['signal_vetoed'] is False
            and finalization['wall_seconds'] <= 5 and not finalization['errors'],
            'PHASE_CHILD_INFRASTRUCTURE_NOT_GENUINELY_FINALIZED')
    return rc


def main(args):
    require(sys.platform == 'linux' and sys.version_info[:2] in ((3, 11), (3, 12))
            and os.getuid() == os.geteuid() != 0 and hasattr(os, 'wait4'), 'FROZEN_LINUX_NONROOT311_OR312_WAIT4_REQUIRED')
    root, output = safe_path(args.repo_root), safe_path(args.output_root)
    require(not output.exists() and not output.is_relative_to(root) and not root.is_relative_to(output)
            and output.parent.is_dir() and output.parent.lstat().st_dev == root.lstat().st_dev,
            'FRESH_EXTERNAL_DISK_BACKED_OUTPUT_ON_CHECKOUT_FILESYSTEM_REQUIRED')
    require(not (root/'.env').exists() and not any(name.startswith(('POROTA_', 'PAPER_')) or name in
            ('DATA_DIR', 'HIST_DB_PATH') for name in os.environ), 'OPERATIONAL_ENVIRONMENT_FORBIDDEN')
    output.mkdir(mode=0o700)
    runner_raw, runner_stat = capture(__file__)
    publish(output/'external-runner.py', runner_raw)
    launcher = {'schema': 'rc6.frozen-governed-external-launch.v2', 'execution_id': uuid.uuid4().hex,
        'candidate_sha': args.source_sha, 'candidate_tree': args.source_tree, 'uid': os.geteuid(),
        'python': sys.version, 'interpreter': sys.executable, 'runner_sha256': digest(runner_raw),
        'runner_stat_fields': runner_stat, 'scope': 'repository-root automatic pytest discovery',
        'phase_timeout_seconds': args.timeout_seconds, 'cache_provider_disabled': True,
        'plugin_autoload_disabled': True, 'phase_infrastructure_plugin': 'EXTERNAL_DECLARED_NODE_AND_OBSERVATION_RECORDER_ONLY'}
    env = {**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
    env.pop('PYTHONPATH', None)
    common = [sys.executable, '-I', '-B', str(safe_path(__file__)), '--repo-root', str(root),
        '--source-sha', args.source_sha, '--source-tree', args.source_tree, '--output-root', str(output),
        '--timeout-seconds', str(args.timeout_seconds)]
    try:
        before = source_pin(root, args.source_sha, args.source_tree)
        closure = installed_closure(root)
        test_args, exclusions, inventory = approved_scope(root, args.source_sha)
        publish(output/'source-before.index.json', canonical(before))
        publish(output/'convergence-inventory.json', canonical(inventory))
        launcher.update({'source_before_index_sha256': digest(canonical(before)),
            'closure_before_collection_and_fixture': closure,
            'test_arguments_from_original_policy': test_args, 'exclusions': exclusions})
        publish(output/'launch.json', canonical(launcher))
        phases = {}
        for phase in ('collection', 'execution'):
            phase_before = source_pin(root, args.source_sha, args.source_tree)
            compare_source(before, phase_before)
            publish(output/(phase+'.parent-source-before.index.json'), canonical(phase_before))
            phases[phase] = subprocess_phase(common+['--phase', phase], root, output/(phase+'.log'), env, args.timeout_seconds)
            publish(output/(phase+'.kernel.json'), canonical(phases[phase]))
            phase_after = source_pin(root, args.source_sha, args.source_tree)
            compare_source(before, phase_after)
            publish(output/(phase+'.parent-source-after.index.json'), canonical(phase_after))
            require(phases[phase]['returncode'] == 0 and phases[phase]['timed_out'] is False,
                    'ACTUAL_GOVERNED_PHASE_NOT_GREEN:'+phase)
            require(phases[phase]['actual_child_reaped'] is True
                    and phases[phase]['wait4_reaped_pid'] == phases[phase]['pid']
                    and phases[phase]['kernel_pre_popen_echild_verified'] is True
                    and phases[phase]['owned_children_exhaustion_verified'] is True
                    and phases[phase]['kernel_wait4_zero_observed_irreversible_red'] is False
                    and phases[phase]['late_observed_main_reap_irreversible_red'] is False
                    and phases[phase]['actual_launch_to_pid_reap_wall_seconds'] <= args.timeout_seconds
                    and phases[phase]['process_group_absent_at_main_reap'] is True
                    and phases[phase]['process_group_absent_after_reap'] is True
                    and phases[phase]['subreaper_restore_attempted_on_all_activated_paths'] is True
                    and phases[phase]['remaining_owned_children'] == []
                    and phases[phase]['residual_descendants_observed'] == []
                    and phases[phase]['supervisor_errors'] == [],
                    'ACTUAL_GOVERNED_PHASE_OWNED_CHILD_CUSTODY_NOT_CLOSED:'+phase)
        collect = json.loads(capture(output/'collection.observations.json')[0])
        execution = json.loads(capture(output/'execution.observations.json')[0])
        require(all(row['offline_inet_creation_capability']['status'] == 'INSTALLED_AND_KERNEL_WITNESSED'
                    and row['child_infrastructure_finalization']['status'] == 'GREEN'
                    and row['child_infrastructure_finalization']['finalization_thread_finished'] is True
                    and row['child_infrastructure_finalization']['kernel_echild_before_phase_return'] is True
                    and row['child_infrastructure_finalization']['signal_guard_installed_and_witnessed'] is True
                    and not row['child_infrastructure_finalization']['termination_signal_attempts']
                    and row['child_infrastructure_finalization']['forced_termination_attempted'] is False
                    and row['child_infrastructure_finalization']['signal_vetoed'] is False
                    and row['child_infrastructure_finalization']['wall_seconds'] <= 5
                    and not row['child_infrastructure_finalization']['errors']
                    for row in (collect, execution)), 'PHASE_OFFLINE_CAPABILITY_OR_CHILD_FINALIZATION_RED')
        require(collect['items'] == execution['items'] and collect['items'], 'COLLECTION_EXECUTION_NODE_IDENTITY_MISMATCH')
        from scripts import rc6_convergence_provenance as verifier
        junit = verifier.capture_junit(output/'porota-governed-tests.xml')
        _suite, cases = verifier.governed_junit_cases(junit)
        _guard_counts, count = verifier.executed_cases(junit)
        require(count == len(collect['items']) and Counter((row['classname'], row['name']) for row in collect['items'])
                == Counter((case.get('classname'), case.get('name')) for case in cases), 'NATIVE_JUNIT_CASE_COLLECTION_MISMATCH')
        after = source_pin(root, args.source_sha, args.source_tree)
        publish(output/'source-after.index.json', canonical(after))
        atime = compare_source(before, after)
        require(all(not row['inet_socket_attempts'] and not row['unexpected_product_imports']
                    for row in (collect, execution)), 'GOVERNED_NETWORK_OR_PRODUCT_IMPORT_OBSERVATION_RED')
        proof = {'schema_version': 1, 'candidate_sha': args.source_sha, 'candidate_tree': args.source_tree,
            'junit_sha256': junit.sha256, 'junit_bytes': len(junit.data), 'source_unchanged': True,
            'scope': 'repository-root automatic pytest discovery', 'discovered': count, 'executed': count,
            'skipped': 0, 'xfail': 0, 'failures': 0, 'errors': 0, 'exclusions': exclusions,
            'pytest_exit_code': 0, 'status': 'GREEN'}
        publish(output/'porota-governed-tests.json', canonical(proof))
        report = {**proof, 'schema': 'rc6.frozen-governed-external-receipt.v2', 'execution_id': launcher['execution_id'],
            'python': sys.version, 'interpreter': sys.executable, 'uid': os.geteuid(), 'actual_phase_resources': phases,
            'runner_sha256': digest(runner_raw), 'source_before_index_sha256': digest(canonical(before)),
            'source_after_index_sha256': digest(canonical(after)), 'tracked_files': len(before['files']), 'overlay_count': 0,
            'physical_directories': len(before['physical_directories']), 'source_namespace_exact_before_each_phase_and_after': True,
            'git_metadata_exclusion': before['git_metadata'],
            'source_bytes_modes_blobs_unchanged': True, 'stable_code_custody_fields_unchanged': list(STABLE_CODE_FIELDS),
            'observed_code_atime_changes': atime, 'all_eleven_code_stat_fields_unchanged_claimed': False,
            'all_source_database_stat_custody_claimed_by_this_driver': False,
            'native_data_custody': 'ASSERTED_BY_ACTUALLY_EXECUTED_NATIVE_GUARDS_NOT_REDEFINED_BY_GOV_DRIVER',
            'network_observation_scope': execution['inet_observation_scope'],
            'offline_inet_creation_capability': execution['offline_inet_creation_capability'],
            'inet_socket_constructor_requests': {
                'collection': collect['inet_socket_constructor_requests'],
                'execution': execution['inet_socket_constructor_requests']},
            'phase_writable_namespaces': {'collection': collect['phase_namespace'], 'execution': execution['phase_namespace']},
            'transitive_child_kernel_network_attestation': False, 'immutable_image_artifact_or_runtime_validated': False,
            'final_candidate_eligibility': 'RECOMPUTE_CURRENT_FIP_AND_JOIN_RAW_GOV_JUNIT_BEFORE_ANY_BUILD'}
        publish(output/'governed-execution.receipt.json', canonical(report))
        files = {path.name: {'sha256': digest(capture(path)[0]), 'bytes': path.stat().st_size}
                 for path in output.iterdir() if path.is_file()}
        publish(output/'raw-output.manifest.json', canonical({'source_sha': args.source_sha, 'source_tree': args.source_tree, 'files': files}))
        print(json.dumps({'status': 'GREEN', 'executed': count, 'execution_id': launcher['execution_id'],
                          'raw_root': str(output), 'source_sha': args.source_sha, 'junit_sha256': junit.sha256}))
        return 0
    except BaseException as error:
        publish(output/'governed-error.json', canonical({'status': 'RED', 'exception_class': type(error).__name__,
            'reason': str(error), 'source_sha': args.source_sha, 'source_tree': args.source_tree,
            'execution_id': launcher['execution_id'], 'raw_logs_preserved': True}))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', required=True)
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--source-tree', required=True)
    parser.add_argument('--output-root', required=True)
    parser.add_argument('--timeout-seconds', type=int, default=5400)
    parser.add_argument('--phase', choices=('collection', 'execution'), help=argparse.SUPPRESS)
    options = parser.parse_args()
    require(re.fullmatch(r'[0-9a-f]{40}', options.source_sha) and re.fullmatch(r'[0-9a-f]{40}', options.source_tree),
            'LITERAL_SOURCE_SHA_TREE_REQUIRED')
    require(60 <= options.timeout_seconds <= 5400, 'BOUNDED_PHASE_TIMEOUT_REQUIRED')
    old_umask = os.umask(0o022)
    try:
        raise SystemExit(phase_child(options) if options.phase else main(options))
    finally:
        os.umask(old_umask)
