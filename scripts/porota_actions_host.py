#!/usr/bin/env python3
"""Execute the versioned RC6 host procedure from GitHub Actions over strict SSH."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


class HostFailure(ValueError):
    def __init__(self, reason, receipt):
        super().__init__(reason)
        self.receipt = receipt


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def connection_values(env):
    host, user = env.get('DO_HOST', ''), env.get('DO_USER', '')
    key, known = env.get('DO_SSH_KEY', ''), env.get('DO_KNOWN_HOSTS', '')
    require(bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.:-]{0,252}', host))
            and bool(re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', user))
            and bool(key.strip()) and bool(known.strip()), 'SSH_CONFIGURATION_INVALID')
    return host, user, key, known


def decode_receipt(raw):
    require(len(raw) <= 131072, 'HOST_RECEIPT_OVERSIZE')
    def distinct(items):
        result = {}
        for key, value in items:
            require(key not in result, 'HOST_RECEIPT_DUPLICATE_KEY')
            result[key] = value
        return result
    def reject(_):
        raise ValueError('HOST_RECEIPT_NONFINITE')
    result = json.loads(raw, object_pairs_hook=distinct, parse_constant=reject)
    require(type(result) is dict, 'HOST_RECEIPT_NOT_OBJECT')
    return result


def validate_stage(path):
    require(isinstance(path, str) and bool(re.fullmatch(
        r'/[A-Za-z0-9_./-]+/\.local/state/porota-rc6/stage\.[A-Za-z0-9_-]+', path))
        and '..' not in Path(path).parts, 'REMOTE_STAGE_INVALID')


def validate_runtime_receipt(receipt, release, returncode):
    require(returncode == 0, 'HOST_COMMAND_FAILED')
    require(receipt.get('schema') == 'porota.rootless.deploy.v1'
            and receipt.get('state') == 'VALIDADO_RUNTIME' and receipt.get('phase') == 'COMPLETE',
            'RUNTIME_VALIDATION_INCOMPLETE')
    require(all(receipt.get(key) == release[key] for key in ('source_sha', 'source_tree', 'image_id')),
            'RUNTIME_RELEASE_MISMATCH')
    require(receipt.get('operating_mode') == 'PRODUCTION_PAPER'
            and receipt.get('execution_mode') == 'SIMULATION'
            and type(receipt.get('real_orders_sent')) is int and receipt['real_orders_sent'] == 0,
            'RUNTIME_PAPER_ZERO_UNVERIFIED')
    require(receipt.get('ppi_watch_touched') is False and receipt.get('boca_touched') is False,
            'PROTECTED_SCOPE_UNVERIFIED')
    roles = receipt.get('roles', {})
    require(set(roles) == {'observer', 'dashboard'} and all(
        isinstance(cid, str) and re.fullmatch(r'[0-9a-f]{64}', cid) for cid in roles.values()),
        'RUNTIME_CONTAINERS_UNVERIFIED')
    health = receipt.get('health', {})
    require(health.get('dashboard') == 'healthy' and health.get('observer') == 'running'
            and health.get('orders_guard') == 'BLOCKED', 'RUNTIME_HEALTH_UNVERIFIED')
    require(receipt.get('cleanup', {}).get('state') == 'COMPLETE', 'RUNTIME_CLEANUP_INCOMPLETE')


@contextmanager
def connection(env):
    host, user, key, known = connection_values(env)
    with tempfile.TemporaryDirectory(prefix='porota-ssh-') as temporary:
        keypath, hostpath = Path(temporary)/'key', Path(temporary)/'known_hosts'
        keypath.write_text(key.rstrip()+'\n')
        hostpath.write_text(known.rstrip()+'\n')
        keypath.chmod(0o600)
        hostpath.chmod(0o600)
        options = ['-F', '/dev/null', '-i', str(keypath), '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
                   '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile='+str(hostpath),
                   '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3']
        yield ['ssh', *options, user+'@'+host], ['scp', *options], user+'@'+host


def ssh_run(ssh, command, *, data=None, timeout=180):
    try:
        return subprocess.run([*ssh, shlex.join([str(x) for x in command])], input=data,
                              capture_output=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        raise ValueError('SSH_TRANSPORT_FAILED') from None


STAGE_CREATE = '''import os,pwd,pathlib,shutil,stat,tempfile,sys
uid=os.getuid()
assert uid==os.geteuid() and uid>0
home=pathlib.Path(pwd.getpwuid(uid).pw_dir)
base=home/'.local/state/porota-rc6'
assert not any(p.is_symlink() for p in (base,*base.parents))
base.mkdir(mode=0o700,parents=True,exist_ok=True)
meta=base.stat()
assert meta.st_uid==uid and not meta.st_mode & 0o077
store=home/'.local/share/porota-rc6/docker'
probe=store
while not probe.exists(): probe=probe.parent
mode=sys.argv[2] if len(sys.argv)>2 else 'release'
assert mode in ('release','staging-only')
for path in ((base,) if mode=='staging-only' else (base,probe)):
 assert shutil.disk_usage(path).free>=int(sys.argv[1])
print(tempfile.mkdtemp(prefix='stage.',dir=base))
'''

STAGE_REMOVE = '''import os,pathlib,shutil,stat,sys
p=pathlib.Path(sys.argv[1])
assert p.parent==pathlib.Path.home()/'.local/state/porota-rc6' and p.name.startswith('stage.')
assert not any(x.is_symlink() for x in (p,*p.parents))
assert p.stat().st_uid==os.getuid() and stat.S_ISDIR(p.stat().st_mode)
shutil.rmtree(p)
'''


def execute(operation, repository, release_dir=None, *, env=None):
    env = env or os.environ
    require(operation in {'inspect', 'prepare', 'deploy', 'migrate'}, 'HOST_OPERATION_INVALID')
    repository = Path(repository)
    with connection(env) as (ssh, scp, target):
        if operation == 'inspect':
            proc = ssh_run(ssh, ['python3', '-B', '-', 'inspect', '--root', '/opt/porota-trading'],
                           data=(repository/'scripts/porota_rootless_setup.py').read_bytes())
            result = decode_receipt(proc.stdout)
            require(proc.returncode == 0 and result.get('host_mutations') == 0
                    and result.get('schema') == 'porota.rootless.inspect.v1', 'HOST_INSPECTION_UNVERIFIED')
            return result
        stage = None
        result = None
        host_started = host_finished = False
        with tempfile.TemporaryDirectory(prefix='porota-control-source-') as temporary:
            if operation in {'deploy', 'migrate'}:
                require(release_dir is not None, 'RELEASE_DIRECTORY_REQUIRED')
                release_dir = Path(release_dir)
                release = decode_receipt((release_dir/'release.json').read_bytes())
                source = release_dir.parent/'source'
                require(source.is_dir() and not source.is_symlink(), 'VERIFIED_SOURCE_REQUIRED')
                from scripts.porota_release import verify_release
                verify_release(release_dir, expected_sha=release['source_sha'], expected_tree=release['source_tree'])
                required = (release['members']['porota-predeploy-image.tar.gz']['bytes']
                            + 2*release['image_size_bytes']
                            + 2*release['members']['porota-deploy-bundle-v2.tgz']['bytes'] + 2147483648)
            else:
                source = Path(temporary)/'source'
                (source/'scripts').mkdir(parents=True)
                for name in ('porota_rootless_setup.py', 'porota_rootless_host.py'):
                    shutil.copyfile(repository/'scripts'/name, source/'scripts'/name)
                required = 2147483648
            # Actual free bytes on staging and Docker filesystems BEFORE upload.
            required += sum(p.stat().st_size for p in source.rglob('*') if p.is_file())
            created = ssh_run(ssh, ['python3', '-I', '-B', '-c', STAGE_CREATE, str(required)])
            require(created.returncode == 0, 'HOST_STAGING_OR_DISK_PREFLIGHT_FAILED')
            stage = created.stdout.decode().strip()
            validate_stage(stage)
            try:
                upload = [str(source)]
                if release_dir is not None:
                    require(Path(release_dir).name == 'release', 'RELEASE_DIRECTORY_NAME_INVALID')
                    upload.append(str(release_dir))
                try:
                    sent = subprocess.run([*scp, '-r', *upload, target+':'+stage+'/'],
                                          capture_output=True, timeout=900)
                except (OSError, subprocess.TimeoutExpired):
                    raise ValueError('RELEASE_TRANSFER_FAILED') from None
                require(sent.returncode == 0, 'RELEASE_TRANSFER_FAILED')
                helper = 'porota_rootless_host.py' if operation == 'deploy' else 'porota_rootless_setup.py'
                command = ['python3', '-B', stage+'/source/scripts/'+helper, operation,
                           '--root', '/opt/porota-trading']
                if operation in {'deploy', 'migrate'}:
                    attempt = env.get('GITHUB_RUN_ID', '')+'-'+env.get('GITHUB_RUN_ATTEMPT', '')
                    require(bool(re.fullmatch(r'[1-9][0-9]*-[1-9][0-9]*', attempt)), 'ACTIONS_ATTEMPT_INVALID')
                    command += ['--release-dir', stage+'/release', '--source-sha', release['source_sha'],
                                '--source-tree', release['source_tree'], '--attempt', attempt]
                host_started = True
                try:
                    # The host permits 10 minutes for image loading and 20 for
                    # startup. Leave time for verification and final receipts.
                    proc = ssh_run(ssh, command, timeout=2400)
                    if proc.returncode == 255:
                        raise ValueError('SSH_TRANSPORT_FAILED')
                except ValueError as exc:
                    result = {'state': 'NO_VERIFICADO', 'phase': 'TRANSPORT',
                              'staging_name': Path(stage).name,
                              'transfer_cleanup': 'RETAINED_COMMAND_OUTCOME_UNKNOWN'}
                    raise HostFailure(str(exc), result) from exc
                host_finished = True
                result = decode_receipt(proc.stdout)
                if operation in {'deploy', 'migrate'}:
                    try:
                        validate_runtime_receipt(result, release, proc.returncode)
                    except ValueError as exc:
                        raise HostFailure(str(exc), result) from exc
                else:
                    if proc.returncode != 0:
                        raise HostFailure('ROOTLESS_PREPARATION_FAILED', result)
                return result
            finally:
                # Disconnecting SSH does not prove the remote writer stopped.
                # Preserve its source and package until a read-only observation
                # resolves that outcome; never delete live inputs on timeout.
                if stage and not (host_started and not host_finished):
                    pending_error = sys.exc_info()[1]
                    try:
                        cleaned = ssh_run(ssh, ['python3', '-I', '-B', '-c', STAGE_REMOVE, stage])
                        cleanup_state = ('COMPLETE' if cleaned.returncode == 0 else
                                         'NO_VERIFICADO' if cleaned.returncode == 255 else 'INCOMPLETE')
                    except ValueError:
                        cleanup_state = 'NO_VERIFICADO'
                    if result is not None:
                        result['transfer_cleanup'] = cleanup_state
                    if cleanup_state != 'COMPLETE' and pending_error is None:
                        raise HostFailure('TRANSFER_CLEANUP_INCOMPLETE', result or {
                            'state': 'NO_VERIFICADO', 'staging_name': Path(stage).name,
                            'transfer_cleanup': cleanup_state})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('inspect', 'prepare', 'deploy', 'migrate'))
    parser.add_argument('--repository', type=Path, default=Path.cwd())
    parser.add_argument('--release-dir', type=Path)
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = execute(args.operation, args.repository, args.release_dir)
        code = 0
    except (ValueError, OSError, KeyError) as exc:
        # No raw command, connection value, credential or stderr is published.
        reason = str(exc) if isinstance(exc, ValueError) and re.fullmatch(r'[A-Z_]+', str(exc)) else type(exc).__name__
        result = exc.receipt if isinstance(exc, HostFailure) else {}
        result.update(state='BLOQUEADO', transport_failure=reason)
        result.setdefault('real_orders_sent', None)
        code = 1
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(result, sort_keys=True)+'\n')
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(code)


if __name__ == '__main__':
    main()
