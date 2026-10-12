"""Prepared external Source-C runner; no execution has been performed on this source.

Run once per approved frozen interpreter and whole Git worktree. There is no
receipt-only mode, test allowlist, skip mode, installer, image build or deploy.
Python socket observations cover this process only; nested native guards keep
their own instrumentation. Installed metadata does not attest wheel bytes.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import sys
import time
import uuid

FIELDS = ('st_dev', 'st_ino', 'st_uid', 'st_gid', 'st_mode', 'st_nlink',
          'st_size', 'st_blocks', 'st_atime_ns', 'st_mtime_ns', 'st_ctime_ns')
STABLE_CODE_FIELDS = tuple(field for field in FIELDS if field != 'st_atime_ns')
MAX_SOURCE_FILE = 128*1024**2
MAX_SOURCE_MEMBERS = 50000


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
    started = time.monotonic(); interrupted = None
    with output.open('xb') as stream:
        process = subprocess.Popen(command, cwd=root, env=env, stdout=stream,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        while True:
            pid, status, usage = os.wait4(process.pid, os.WNOHANG)
            if pid:
                process.returncode = os.waitstatus_to_exitcode(status); break
            if time.monotonic()-started > limit and interrupted is None:
                os.killpg(process.pid, signal.SIGINT); interrupted = time.monotonic()
            if interrupted is not None and time.monotonic()-interrupted > 20:
                os.killpg(process.pid, signal.SIGKILL)
                pid, status, usage = os.wait4(process.pid, 0)
                process.returncode = os.waitstatus_to_exitcode(status); break
            time.sleep(.1)
    return {'pid': process.pid, 'returncode': process.returncode, 'timed_out': interrupted is not None,
            'wall_seconds': time.monotonic()-started, 'cpu_user_seconds': usage.ru_utime,
            'cpu_system_seconds': usage.ru_stime, 'peak_rss_bytes': usage.ru_maxrss*1024,
            'scope': 'EXACT_PYTEST_PHASE_PID_AND_KERNEL_ACCOUNTED_REAPED_DESCENDANTS; NOT SUM_OF_CONCURRENT_RSS',
            'command': command}


def phase_child(args):
    root, output = safe_path(args.repo_root), safe_path(args.output_root)
    pin = source_pin(root, args.source_sha, args.source_tree)
    closure = installed_closure(root)
    runner_raw, _ = capture(__file__)
    launcher = json.loads(capture(output/'launch.json')[0])
    require(launcher['runner_sha256'] == digest(runner_raw), 'CHILD_RUNNER_CHANGED')
    test_args, exclusions, _inventory = approved_scope(root, args.source_sha)
    observations = {'inet_socket_attempts': [], 'subprocess_executable_counts': {}, 'actual_product_imports': [],
                    'unexpected_product_imports': [], 'source_files': len(pin['files'])}
    def observe(event, arguments):
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
    import pytest
    from _pytest.junitxml import mangle_test_address
    items = []
    class Observer:
        def pytest_collection_finish(self, session):
            for item in session.items:
                address = mangle_test_address(item.nodeid)
                items.append({'nodeid': item.nodeid, 'classname': '.'.join(address[:-1]), 'name': address[-1]})
    argv = ['-q', '-p', 'no:cacheprovider', '-o', 'pythonpath=.', '-o', 'junit_family=legacy', *test_args]
    if args.phase == 'collection':
        argv.append('--collect-only')
    else:
        argv.extend(['--junitxml='+str(output/'porota-governed-tests.xml'), '--basetemp='+str(output/'pytest-private')])
    rc = int(pytest.main(argv, plugins=[Observer()]))
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
        **observations, 'inet_observation_scope': 'THIS_PYTEST_PYTHON_PROCESS_DNS_INET_SOCKET_AUDIT_AND_KNOWN_CLIENT_EXECUTABLES; NOT A KERNEL_NETWORK_NAMESPACE_OR TRANSITIVE_CHILD_NETWORK_ATTESTATION',
        'import_observation_scope': 'ACTUAL_PRODUCT_MODULES_PRESENT_AT_PHASE_EXIT; NESTED_NATIVE_GUARDS_BIND_THEIR_OWN_SOURCE'}
    publish(output/(args.phase+'.observations.json'), canonical(report))
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
        '--source-sha', args.source_sha, '--source-tree', args.source_tree, '--output-root', str(output)]
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
        collect = json.loads(capture(output/'collection.observations.json')[0])
        execution = json.loads(capture(output/'execution.observations.json')[0])
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
