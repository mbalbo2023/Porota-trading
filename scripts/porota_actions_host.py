#!/usr/bin/env python3
"""Execute the versioned RC6 host procedure from GitHub Actions over strict SSH."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
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
RELEASE_COORDINATES = ('source_sha', 'source_tree', 'image_id', 'attempt')
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


def validate_runtime_receipt(receipt, release, returncode, *, download=None):
    require(returncode == 0, 'HOST_COMMAND_FAILED')
    require(receipt.get('schema') == 'porota.rootless.deploy.v1'
            and receipt.get('state') == 'VALIDADO_RUNTIME' and receipt.get('phase') == 'COMPLETE',
            'RUNTIME_VALIDATION_INCOMPLETE')
    require(all(receipt.get(key) == release[key] for key in ('source_sha', 'source_tree', 'image_id')),
            'RUNTIME_RELEASE_MISMATCH')
    if download:
        require(all(receipt.get(key) == value for key, value in download.items()),
                'RUNTIME_DEPLOYMENT_PROVENANCE_MISMATCH')
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


def deployment_phases(receipt, *, host_verified=False):
    phases = _evidence_row(receipt.get('deployment_phases'))
    receipt['deployment_phases'] = phases
    for name, confirmed in (('physical_installation', 'DESPLEGADO'), ('initial_health', 'VALIDADO_RUNTIME')):
        if host_verified:
            phases.setdefault(name, {'state': confirmed, 'receipt_observed_at': datetime.now(timezone.utc).isoformat()})
        else:
            phases.setdefault(name, {'state': 'NO_VERIFICADO'})
    return phases


def _matches_installed(proof, receipt):
    return all(key in receipt and proof.get(key) == receipt[key] for key in RELEASE_COORDINATES)


def _evidence_row(value):
    return value if type(value) is dict else {}


def audit_promotion(receipt, dashboard, *, operation, host_succeeded, dashboard_succeeded):
    """Record Actions' integral verdict without rewriting the physical result."""
    phases = deployment_phases(receipt)
    checks = {}
    if not host_succeeded:
        state, reason = 'BLOQUEADO', 'HOST_PROCEDURE_INCOMPLETE'
    elif operation == 'migrate' or receipt.get('market_provider') != 'IOL_REST':
        state, reason = 'NO_VERIFICADO', 'INTEGRAL_IOL_AUDIT_NOT_APPLIED'
    else:
        inventory = _evidence_row(receipt.get('installed_inventory'))
        scheduler = _evidence_row(inventory.get('scheduler'))
        metrics = _evidence_row(inventory.get('runtime_metrics'))
        replay = _evidence_row(receipt.get('synthetic_replay'))
        retired_units = scheduler.get('operational_ppi_units')
        requested = scheduler.get('requested_units')
        checks = {
            'physical_and_initial_health': (receipt.get('state') == 'VALIDADO_RUNTIME'
                and _evidence_row(phases['physical_installation']).get('state') == 'DESPLEGADO'
                and _evidence_row(phases['initial_health']).get('state') == 'VALIDADO_RUNTIME'),
            'installed_inventory': (_matches_installed(inventory, receipt) and _matches_installed(metrics, receipt)
                and scheduler.get('state') == 'OBSERVED' and type(requested) is int and requested > 0
                and requested == scheduler.get('observed_units')
                and isinstance(retired_units, dict)
                and all(value == 'INACTIVE_OR_ABSENT' for value in retired_units.values())
                and metrics.get('state') == 'OBSERVED'
                and type(metrics.get('real_orders_sent')) is int and metrics['real_orders_sent'] == 0),
            'critical_scheduler_bindings': _evidence_row(scheduler.get('critical_bindings')).get('state') == 'VALIDADO_RUNTIME',
            'isolated_replay': (_matches_installed(replay, receipt) and replay.get('status') == 'PASS'
                and _evidence_row(replay.get('multi_instrument_replay')).get('status') == 'PASS'
                and replay.get('network') == 'NONE'
                and type(replay.get('operational_mounts')) is int and replay['operational_mounts'] == 0
                and type(replay.get('real_orders_sent')) is int and replay['real_orders_sent'] == 0),
            'dashboard_playwright': (dashboard_succeeded and _matches_installed(dashboard, receipt)
                and dashboard.get('state') == 'VERIFICADO' and dashboard.get('schema') == 'porota.dashboard.playwright.v1'
                and dashboard.get('dashboard_cid') == _evidence_row(receipt.get('roles')).get('dashboard')
                and dashboard.get('read_only') is True
                and type(dashboard.get('real_orders_sent')) is int and dashboard['real_orders_sent'] == 0),
        }
        if all(checks.values()):
            state, reason = 'VALIDADO_RUNTIME', None
        else:
            state, reason = 'BLOQUEADO', 'INTEGRAL_POSTFLIGHT_INCOMPLETE'
    phases['integral_audit'] = {'state': state, 'reason': reason, 'scope': 'DEPLOY_V2_POSTFLIGHT',
                                'observed_at': datetime.now(timezone.utc).isoformat(), 'checks': checks}
    return state != 'BLOQUEADO'


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


SCHEDULER_UNIT_PROBE = '''import os,pathlib,pwd,sys
account=pwd.getpwuid(os.getuid())
assert account.pw_uid==os.geteuid()>0 and len(sys.argv)==2
roles={'health':('porota-iol-oauth-health-rc6.service','--verify-iol-rest'),
       'paper_opening':('porota-preopen-rc6.service','--revalidate-paper-gate')}
unit=sys.argv[1]
launcher=pathlib.Path(account.pw_dir)/'.local/state/porota-rc6/scheduler-dispatch.py'
args=['/usr/bin/python3','-B',str(launcher),'--unit',*roles[unit]]
os.chdir('/opt/porota-trading')
os.execve(args[0],args,{'HOME':account.pw_dir,'USER':account.pw_name,'LOGNAME':account.pw_name,
           'PATH':'/usr/bin:/bin:/usr/sbin:/sbin','POROTA_RUNTIME_BACKEND':'ROOTLESS','PYTHONDONTWRITEBYTECODE':'1'})
'''


def iol_rest_units(ssh, installer, mode):
    result = ssh_run(ssh, ['/usr/bin/sudo', '-n', '--', '/usr/bin/python3', '-I', '-B', '-', mode],
                     data=installer, timeout=120)
    receipt = decode_receipt(result.stdout) if result.stdout else {}
    require(result.returncode == 0 and receipt.get('source') == 'IOL_REST'
            and receipt.get('state') == ('EFFECTIVE' if mode == 'install' else 'PREFLIGHT_OK'),
            receipt.get('reason', 'IOL_UNIT_' + mode.upper() + '_UNVERIFIED'))
    return receipt


def scheduler_postflight(ssh, release, role, source):
    unverified = {'state': 'NO_VERIFICADO', 'source': source, 'role': role}
    try:
        proc = ssh_run(ssh, ['/usr/bin/python3', '-I', '-B', '-c', SCHEDULER_UNIT_PROBE, role], timeout=220)
    except ValueError:
        return {**unverified, 'reason': 'SCHEDULER_PROBE_TRANSPORT_UNAVAILABLE'}
    try:
        receipt = decode_receipt(proc.stdout)
    except ValueError:
        return {**unverified, 'reason': 'SCHEDULER_PROBE_RECEIPT_INVALID'}
    for name, reason in (('source_sha', 'RUNTIME_RELEASE_MISMATCH'),
                         ('image_id', 'RUNTIME_RELEASE_MISMATCH'),
                         ('real_orders_sent', 'RUNTIME_PAPER_ZERO_UNVERIFIED')):
        if name in receipt:
            expected = 0 if name == 'real_orders_sent' else release[name]
            require(type(receipt[name]) is type(expected) and receipt[name] == expected, reason)
    failure = receipt.get('failure_signature')
    if failure in ('SCHEDULER_DAEMON_CHANGED', 'SCHEDULER_RUNTIME_STOPPED', 'PAPER_DATABASE_SAFETY',
                   'DATABASE_REPLACED', 'IOL_RELEASE_DATABASE_IDENTITY_UNVERIFIED',
                   'IOL_RELEASE_DATABASE_IDENTITY_CHANGED', 'PAPER_RELEASE_DATABASE_IDENTITY_UNVERIFIED',
                   'PAPER_RELEASE_DATABASE_IDENTITY_CHANGED'):
        raise ValueError(failure)
    commands = receipt.get('commands') or []
    if (proc.returncode in (0, 2) and receipt.get('state') == 'EXECUTED'
            and receipt.get('schema') == 'porota.rootless.job.v1'
            and all(key in receipt for key in ('source_sha', 'image_id', 'real_orders_sent'))
            and isinstance(commands, list) and len(commands) == 1 and type(commands[0]) is dict
            and commands[0].get('source') == source and type(commands[0].get('exit_code')) is int
            and commands[0]['exit_code'] == proc.returncode):
        evidence = {'state': 'OBSERVED', 'source': source, 'role': role, 'exit_code': proc.returncode}
        for field in ('result', 'reason'):
            code = commands[0].get(field)
            if isinstance(code, str) and re.fullmatch(r'[A-Z_0-9]{1,96}', code):
                evidence[field] = code
        http_status = commands[0].get('http_status')
        if type(http_status) is int and 100 <= http_status <= 599:
            evidence['http_status'] = http_status
        return evidence
    reason = (failure if isinstance(failure, str) and re.fullmatch(r'[A-Z_0-9]{1,96}', failure)
              else 'SCHEDULER_PROBE_CONTRACT_UNVERIFIED')
    return {**unverified, 'reason': reason, 'exit_code': proc.returncode}


def installed_iol_postflight(ssh, stage, release, attempt, operation, *, timeout):
    command = ['python3', '-B', stage+'/source/scripts/porota_rootless_host.py', operation,
               '--source-sha', release['source_sha'], '--source-tree', release['source_tree'], '--attempt', attempt]
    try:
        proc = ssh_run(ssh, command, timeout=timeout)
        proof = decode_receipt(proc.stdout)
    except ValueError:
        return {'status': 'NO_VERIFICADO', 'reason': 'INSTALLED_POSTFLIGHT_TRANSPORT_UNAVAILABLE'}
    if proc.returncode == 0 and all(proof.get(key) == release[key] for key in ('source_sha', 'source_tree', 'image_id')):
        return proof
    return {'status': 'NO_VERIFICADO', 'reason': proof.get('failure_signature', 'INSTALLED_POSTFLIGHT_RECEIPT_UNVERIFIED')}


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
        iol_installer = None
        host_started = host_finished = False
        with tempfile.TemporaryDirectory(prefix='porota-control-source-') as temporary:
            if operation in {'deploy', 'migrate'}:
                require(release_dir is not None, 'RELEASE_DIRECTORY_REQUIRED')
                release_dir = Path(release_dir)
                require(release_dir.name == 'release', 'RELEASE_DIRECTORY_NAME_INVALID')
                release = decode_receipt((release_dir/'release.json').read_bytes())
                source = release_dir.parent/'source'
                require(source.is_dir() and not source.is_symlink(), 'VERIFIED_SOURCE_REQUIRED')
                from scripts.porota_actions_release import git, promotion_source
                from scripts.porota_release import verify_release
                from scripts.porota_rootless_host import Hold, download_evidence, read_regular
                verify_release(release_dir, expected_sha=release['source_sha'], expected_tree=release['source_tree'])
                try:
                    raw_download = read_regular(release_dir.parent/'download.json', 4096)
                    download = download_evidence(raw_download, release)
                    # Reuse the tested-tree check from artifact acquisition and
                    # attest the distinct deployment commit before any upload.
                    require(promotion_source(repository, release['source_sha']) ==
                            (release['source_sha'], release['source_tree'])
                            and download['deployment_sha'] == git(repository, 'rev-parse', 'HEAD'),
                            'DOWNLOAD_DEPLOYMENT_CHECKOUT_MISMATCH')
                except Hold as exc:
                    raise ValueError(str(exc)) from None
                except subprocess.CalledProcessError:
                    raise ValueError('DEPLOYMENT_CHECKOUT_UNVERIFIED') from None
                verified_download = Path(temporary)/'download.json'
                verified_download.write_bytes(raw_download)
                installer_path = source/'scripts/rc6_iol_rest_units.py'
                if installer_path.is_file():
                    iol_installer = read_regular(installer_path, 65536)
                    try:
                        iol_rest_units(ssh, iol_installer, 'preflight')
                    except ValueError as exc:
                        raise HostFailure(str(exc), {'state': 'BLOQUEADO', 'phase': 'IOL_UNIT_PREFLIGHT',
                                                     'failure_signature': str(exc)}) from exc
                required = (release['members']['porota-predeploy-image.tar.gz']['bytes']
                            + 2*release['image_size_bytes']
                            + 2*release['members']['porota-deploy-bundle-v2.tgz']['bytes']
                            + len(raw_download) + 2147483648)
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
                if operation in {'deploy', 'migrate'}:
                    upload.extend((str(release_dir), str(verified_download)))
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
                        validate_runtime_receipt(result, release, proc.returncode, download=download)
                    except ValueError as exc:
                        raise HostFailure(str(exc), result) from exc
                    deployment_phases(result, host_verified=True)
                    if operation == 'deploy' and result.get('market_provider') == 'IOL_REST':
                        result['installed_inventory'] = installed_iol_postflight(ssh, stage, release, attempt,
                                                                                'inventory', timeout=75)
                        result['synthetic_replay'] = installed_iol_postflight(ssh, stage, release, attempt,
                                                                              'replay', timeout=420)
                    try:
                        if operation == 'deploy':
                            result['paper_opening'] = scheduler_postflight(
                                ssh, release, 'paper_opening', 'PAPER_RELEASE_REVALIDATION')
                        if iol_installer is not None:
                            result['iol_rest'] = iol_rest_units(ssh, iol_installer, 'install')
                            # The existing timer owns the collector's DB and output files.
                            result['iol_rest']['collector'] = {'state': 'TIMER_MANAGED'}
                            result['iol_rest']['health'] = scheduler_postflight(ssh, release, 'health', 'IOL_REST')
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


def read_local_receipt(path):
    try:
        return decode_receipt(path.read_bytes())
    except (OSError, ValueError):
        return {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('inspect', 'prepare', 'deploy', 'migrate', 'audit-promotion'))
    parser.add_argument('--repository', type=Path, default=Path.cwd())
    parser.add_argument('--release-dir', type=Path)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--dashboard-receipt', type=Path)
    parser.add_argument('--promotion-operation', choices=('deploy', 'migrate'))
    parser.add_argument('--host-outcome')
    parser.add_argument('--dashboard-outcome')
    args = parser.parse_args()
    if args.operation == 'audit-promotion':
        if args.dashboard_receipt is None or args.promotion_operation is None:
            parser.error('audit-promotion requires --dashboard-receipt and --promotion-operation')
        result = read_local_receipt(args.receipt) or {'state': 'NO_VERIFICADO', 'real_orders_sent': None}
        dashboard = read_local_receipt(args.dashboard_receipt)
        ok = audit_promotion(result, dashboard, operation=args.promotion_operation,
            host_succeeded=args.host_outcome == 'success' and result.get('state') == 'VALIDADO_RUNTIME',
            dashboard_succeeded=args.dashboard_outcome == 'success')
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        pending = args.receipt.with_suffix('.audit.tmp')
        pending.write_text(json.dumps(result, sort_keys=True) + '\n')
        pending.replace(args.receipt)
        print(json.dumps(result['deployment_phases'], sort_keys=True))
        raise SystemExit(0 if ok else 1)
    try:
        result = execute(args.operation, args.repository, args.release_dir)
        code = 0
    except (ValueError, OSError, KeyError) as exc:
        # No raw command, connection value, credential or stderr is published.
        reason = str(exc) if isinstance(exc, ValueError) and re.fullmatch(r'[A-Z_]+', str(exc)) else type(exc).__name__
        result = exc.receipt if isinstance(exc, HostFailure) else {}
        result.update(state='BLOQUEADO', transport_failure=reason)
        if args.operation in {'deploy', 'migrate'}:
            deployment_phases(result)
        result.setdefault('real_orders_sent', None)
        code = 1
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(result, sort_keys=True)+'\n')
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(code)


if __name__ == '__main__':
    main()
