"""A980 OFFHOST OCI diagnostic, deliberately never an admission/release gate.

UNACTIVATED CONTRACT: this file in a candidate checkout is NOT a trust root.
Before any physical use it must be obtained from an independently trusted,
audited SHA/bootstrap and verified by digest, then run with Python -I -S.
Requires an independently reviewed, locally available QA image by
digest; never builds/pulls an image or installs candidate dependencies. The
trusted boundary is the ephemeral runner OS/daemon, this supervisor and image.
Candidate tests run as another UID in a constrained container, never in this
interpreter. Kernel/container escapes are not solved by this module.

HARD HOLD: WS12 never invokes this candidate file; entry exit79 remains.
Local fake-daemon tests prove contracts,
not physical custody. Even a completed invocation returns 79, reports no test
PASS and grants no authority. Raw test evidence never leaves the container.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid


HOLD = 79
TMPFS = {'/work': 'rw,nosuid,nodev,noexec,size=512m,mode=1777',
         '/tmp': 'rw,nosuid,nodev,noexec,size=64m,mode=1777'}
IMAGE_RE = r'[a-z0-9][a-z0-9./:_-]{0,240}@sha256:[0-9a-f]{64}'


def _command(argv: list[str], *, timeout: int) -> str:
    """No shell, inherited credentials/config, stderr or public child output."""
    env = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C',
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
           'GIT_OPTIONAL_LOCKS': '0'}
    with tempfile.TemporaryFile() as output:
        subprocess.run(argv, env=env, stdin=subprocess.DEVNULL, stdout=output,
                       stderr=subprocess.DEVNULL, timeout=timeout, check=True)
        output.seek(0)
        raw = output.read(65537)
    if len(raw) > 65536:
        raise ValueError('RESPONSE_SIZE')
    return raw.decode('utf-8')


def _one(raw: str) -> dict:
    if len(raw) > 65536:
        raise ValueError('RESPONSE_SIZE')
    values = json.loads(raw)
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict):
        raise ValueError('INSPECT_SHAPE')
    return values[0]


def _provenance(repo: Path, sha: str, tree: str) -> None:
    if not all(re.fullmatch(r'[0-9a-f]{40}', x) for x in (sha, tree)):
        raise ValueError('SOURCE_IDENTITY')
    if repo != repo.resolve(strict=True) or not (repo / '.git').is_dir():
        raise ValueError('SOURCE_PATH')
    # No inherited Git config/environment, external diff, hooks or fsmonitor.
    git = ['/usr/bin/git', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null',
           '-C', str(repo)]
    if _command(git + ['rev-parse', 'HEAD'], timeout=10).strip() != sha:
        raise ValueError('SOURCE_SHA')
    if _command(git + ['rev-parse', 'HEAD^{tree}'], timeout=10).strip() != tree:
        raise ValueError('SOURCE_TREE')
    if _command(git + ['status', '--porcelain', '--untracked-files=all', '--ignored'], timeout=10):
        raise ValueError('SOURCE_DIRTY')


def _owned(value: dict, cid: str, nonce: str) -> bool:
    config = value.get('Config')
    labels = config.get('Labels') if isinstance(config, dict) else None
    return (value.get('Id') == cid and isinstance(labels, dict) and
            labels.get('rc6.qa.custody') == nonce)


def _policy(value: dict, repo: Path, cid: str, nonce: str) -> None:
    if not _owned(value, cid, nonce) or value['Config'].get('User') != '65532:65532':
        raise ValueError('CONTAINER_IDENTITY')
    host = value['HostConfig']
    if not isinstance(host, dict):
        raise ValueError('CONTAINER_SHAPE')
    expected = {'Privileged': False, 'ReadonlyRootfs': True, 'NetworkMode': 'none',
                'PidMode': '', 'IpcMode': 'none', 'CgroupnsMode': 'private', 'CapDrop': ['ALL'],
                'PidsLimit': 256, 'Memory': 1073741824, 'MemorySwap': 1073741824,
                'NanoCpus': 2000000000, 'Tmpfs': TMPFS}
    if any(host.get(key) != val for key, val in expected.items()):
        raise ValueError('CONTAINER_POLICY')
    if any(host.get(key) for key in ('CapAdd', 'Devices', 'DeviceRequests',
                                    'Binds', 'VolumesFrom', 'GroupAdd')):
        raise ValueError('CONTAINER_PRIVILEGE')
    if host.get('SecurityOpt') not in (['no-new-privileges'], ['no-new-privileges=true']):
        raise ValueError('CONTAINER_SECURITY')
    log_config = host.get('LogConfig')
    if not isinstance(log_config, dict) or log_config.get('Type') != 'none':
        raise ValueError('CONTAINER_LOGS')
    mounts = value.get('Mounts')
    if not isinstance(mounts, list) or len(mounts) != 1:
        raise ValueError('CONTAINER_MOUNTS')
    mount = mounts[0]
    if not isinstance(mount, dict) or any(mount.get(k) != v for k, v in
           {'Type': 'bind', 'Source': str(repo), 'Destination': '/src', 'RW': False}.items()):
        raise ValueError('CONTAINER_MOUNT')


def run(repo: Path, output: Path, *, image: str, source_sha: str,
        source_tree_sha: str, command=None, report=None) -> int:
    """Produce only a fixed supervisor receipt after owned-container teardown.

    `command` is the Docker transport seam for contract tests. Neither its
    successful mocks nor this receipt establish physical isolation or coverage.
    """
    command = command or _command
    cid = None
    completed = False
    cleaned = False
    nonce = uuid.uuid4().hex
    parent_fd = None
    status = 'INPUT_OR_EXECUTION_UNVERIFIED'
    try:
        if not re.fullmatch(IMAGE_RE, image):
            raise ValueError('IMAGE_REQUIRED')
        repo, output = Path(repo).absolute(), Path(output).absolute()
        if any(c in str(repo) for c in ',\n\r'):
            raise ValueError('MOUNT_PATH')
        if (output.exists() or output.is_symlink() or
                output.parent != output.parent.resolve(strict=True) or
                output.is_relative_to(repo)):
            raise ValueError('OUTPUT_NOT_EXCLUSIVE')
        parent_fd = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        _provenance(repo, source_sha, source_tree_sha)
        # Empty private CLI config prevents automatic proxy/credential injection.
        # Force the local hosted-runner daemon, never DOCKER_HOST/context/TLS.
        with tempfile.TemporaryDirectory(prefix='rc6-qa-docker-config-') as config:
            docker = ['/usr/bin/docker', '--config', config,
                      '--host=unix:///var/run/docker.sock']
            def call(args, timeout=15):
                return command(docker + args, timeout=timeout)
            image_info = _one(call(['image', 'inspect', image]))
            image_config = image_info.get('Config')
            digests = image_info.get('RepoDigests')
            if (not isinstance(image_config, dict) or
                    not isinstance(digests, list) or
                    not all(isinstance(digest, str) for digest in digests) or
                    image not in digests or image_config.get('Volumes')):
                raise ValueError('IMAGE_UNVERIFIED')
            args = ['create', '--pull', 'never', '--label', 'rc6.qa.custody=' + nonce,
                    '--user', '65532:65532', '--read-only', '--network', 'none',
                    '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                    '--pids-limit', '256', '--memory', '1g', '--memory-swap', '1g',
                    '--cpus', '2', '--ipc', 'none', '--cgroupns', 'private',
                    '--log-driver', 'none', '--restart', 'no', '--no-healthcheck',
                    '--mount', f'type=bind,src={repo},dst=/src,readonly,bind-recursive=disabled']
            for path, options in TMPFS.items():
                args += ['--tmpfs', path + ':' + options]
            args += ['--workdir', '/src', '--entrypoint', '/usr/bin/env', image, '-i',
                     'PATH=/usr/local/bin:/usr/bin:/bin', 'HOME=/work',
                     'PYTHONDONTWRITEBYTECODE=1', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD=1',
                     'GIT_CONFIG_NOSYSTEM=1', 'GIT_CONFIG_GLOBAL=/dev/null',
                     'GIT_OPTIONAL_LOCKS=0', 'python3', '-B', '-m',
                     'scripts.rc6_ci_parallel_runner', '--source-sha', source_sha,
                     '--source-tree-sha', source_tree_sha, '--repo', '/src',
                     '--shards', '4', '--diagnostics-dir', '/work/evidence',
                     '--bounded-shard-index', '0', '--batch-limit', '1',
                     '--timeout-seconds', '60']
            raw = call(args).strip()
            if not re.fullmatch(r'[0-9a-f]{64}', raw):
                raise ValueError('CONTAINER_ID')
            cid = raw
            try:
                _policy(_one(call(['inspect', cid])), repo, cid, nonce)
                call(['start', cid])  # No attach: never forward candidate bytes.
                exit_text = call(['wait', cid], timeout=300).strip()
                if not re.fullmatch(r'[0-9]{1,3}', exit_text) or int(exit_text) > 255:
                    raise ValueError('EXIT_UNVERIFIED')
                completed = True
            finally:
                # Exact nonce+CID only; no lists/prune/shared-daemon cleanup.
                if _owned(_one(call(['inspect', cid])), cid, nonce):
                    call(['stop', '--timeout', '5', cid])
                    # Recheck ownership after stop; never force-delete.
                    if not _owned(_one(call(['inspect', cid])), cid, nonce):
                        raise ValueError('TEARDOWN_IDENTITY_CHANGED')
                    call(['rm', cid])
                    cleaned = True
            if not completed or not cleaned:
                raise ValueError('TEARDOWN_UNVERIFIED')
        receipt = {'schema_version': 1, 'status': 'OFFHOST_DIAGNOSTIC_ONLY',
                   'source_sha': source_sha, 'source_tree_sha': source_tree_sha,
                   'image_digest': image.rsplit('@', 1)[1],
                   'container_exit_code': int(exit_text), 'automatic_go': False,
                   'runtime_authority': False, 'full_suite_certified': False,
                   'physical_custody_certified': False}
        # Pytest has no mount of this directory and the container is gone.
        # No file from /work or candidate JSON/JUnit is read by the publisher.
        before = os.fstat(parent_fd)
        current = output.parent.stat(follow_symlinks=False)
        if (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino):
            raise ValueError('OUTPUT_PARENT_CHANGED')
        os.mkdir(output.name, mode=0o700, dir_fd=parent_fd)
        directory_fd = os.open(output.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                               dir_fd=parent_fd)
        try:
            fd = os.open('attestation.json', os.O_WRONLY | os.O_CREAT |
                         os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(receipt, handle, sort_keys=True, separators=(',', ':'))
                handle.write('\n')
                handle.flush()
                os.fsync(handle.fileno())
            os.fsync(directory_fd)
            os.fsync(parent_fd)
            status = 'OFFHOST_DIAGNOSTIC_ONLY'
        finally:
            os.close(directory_fd)
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        # Exception text/paths/daemon responses must never reach public logs.
        if cid is not None and not cleaned:
            status = 'TEARDOWN_UNVERIFIED'
    finally:
        if parent_fd is not None:
            os.close(parent_fd)
    if report is not None:
        report(status)  # Fixed enum only, never str(exception)/candidate output.
    return HOLD


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='OFFHOST diagnostic; always HOLD')
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--image', required=True)
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--source-tree-sha', required=True)
    args = parser.parse_args(argv)
    statuses = []
    result = run(args.repo, args.output, image=args.image, source_sha=args.source_sha,
                 source_tree_sha=args.source_tree_sha, report=statuses.append)
    print('RC6_OCI_QA=BLOQUEADO|reason=' + statuses[0] +
          '|physical_custody_certified=false|automatic_go=false')
    return result


if __name__ == '__main__':
    raise SystemExit(main())
