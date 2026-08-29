"""Etapa la fuente v17 aprobada y construye su imagen; no inicia motores."""
import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import subprocess

VERSION = 'v17-stage-build-1'
ROOT = Path('/opt/porota-trading')
BASE = '612b0431a33909af3eeaaaa909db648c165a4ac9'
TARGET = 'fa79089346d20d20e078d63cba928ca412a64222'
BRANCH = 'feature/v17-convergencia'
IMAGE = 'porota-trading-bot:17.0.0-rc1'
ENGINES = ('porota_trading_bot', 'porota_production_observer')
MIN_AVAILABLE = 5 * 1024 ** 3
BUILD_LOG = Path('/tmp/porota-v17-build.log')


class Stop(Exception):
    pass


def command(args, **kwargs):
    return subprocess.run(args, text=True, timeout=kwargs.pop('timeout', 30), **kwargs)


def git(*args, check=True):
    result = command(['git', '-C', str(ROOT), *args], capture_output=True)
    if check and result.returncode:
        raise Stop('GIT_COMMAND_FAILED')
    return result


def engines():
    template = ('{"name":{{json .Name}},"running":{{json .State.Running}},'
                '"restart":{{json .HostConfig.RestartPolicy.Name}},'
                '"image":{{json .Image}},"user":{{json .Config.User}}}')
    result = command(['sudo','-n','docker','inspect','--type','container',
                      '--format',template,*ENGINES], capture_output=True)
    if result.returncode:
        raise Stop('DOCKER_INSPECT_FAILED')
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    if len(rows) != 2 or {r.get('name') for r in rows} != {'/'+n for n in ENGINES}:
        raise Stop('UNEXPECTED_ENGINE_METADATA')
    if any(r.get('running') is not False or r.get('restart') != 'no' for r in rows):
        raise Stop('ENGINES_MUST_REMAIN_STOPPED_NO_RESTART')
    if any(r.get('user') != 'botuser' or
           not re.fullmatch(r'sha256:[0-9a-f]{64}',str(r.get('image'))) for r in rows):
        raise Stop('ENGINE_INSTALLATION_CHANGED')
    return [{k:r[k] for k in ('name','running','restart','image','user')} for r in rows]


def protected_path(name):
    return (name == '.env' or (name.startswith('.env.') and name != '.env.example') or
            name == '.secrets' or name.startswith('.secrets/') or
            name == 'data' or name.startswith('data/'))


def stage_and_build():
    report = {'diagnostic':VERSION, 'generated_at':datetime.now(timezone.utc).isoformat(),
              'status':'NOT_STARTED', 'stage':'PREFLIGHT', 'source_staged':False,
              'image_built':False, 'containers_created':False, 'engines_started':False,
              'database_created':False, 'database_opened':False,
              'legacy_data_touched':False, 'environment_file_touched':False,
              'promotion_allowed':False, 'rollback_performed':False}
    switched = False
    verified_image = False
    try:
        if os.geteuid() != 1000 or os.getegid() != 1000:
            raise Stop('CALLER_MUST_BE_UID_GID_1000')
        for path in (ROOT, ROOT/'data', ROOT/'data/paper_v17'):
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise Stop('REQUIRED_DIRECTORY_INVALID')
        target_dir = ROOT/'data/paper_v17'
        info = target_dir.lstat()
        if (info.st_uid,info.st_gid,stat.S_IMODE(info.st_mode)) != (1000,1000,0o750):
            raise Stop('V17_DIRECTORY_IDENTITY_CHANGED')
        if any(target_dir.iterdir()):
            raise Stop('V17_DIRECTORY_NOT_EMPTY')
        available = os.statvfs(ROOT/'data').f_bavail * os.statvfs(ROOT/'data').f_frsize
        report['available_bytes_before'] = available
        if available < MIN_AVAILABLE:
            raise Stop('INSUFFICIENT_FREE_SPACE_FOR_BUILD')
        report['engines_before'] = engines()
        if git('rev-parse','HEAD').stdout.strip() != BASE:
            raise Stop('UNEXPECTED_CURRENT_COMMIT')
        if git('branch','--show-current').stdout.strip() != 'testing':
            raise Stop('UNEXPECTED_CURRENT_BRANCH')
        if git('status','--porcelain','--untracked-files=no').stdout.strip():
            raise Stop('TRACKED_WORKTREE_NOT_CLEAN')
        if command(['sudo','-n','docker','image','inspect',IMAGE],
                   capture_output=True).returncode == 0:
            raise Stop('CANDIDATE_IMAGE_ALREADY_EXISTS')
        report['stage'] = 'FETCH'
        git('fetch','--no-tags','origin',BRANCH)
        fetched = git('rev-parse','FETCH_HEAD').stdout.strip()
        report['fetched_commit'] = fetched
        if fetched != TARGET:
            raise Stop('REMOTE_TARGET_CHANGED')
        changed = git('diff','--name-only',BASE,TARGET).stdout.splitlines()
        report['changed_tracked_files'] = len(changed)
        if any(protected_path(name) for name in changed):
            raise Stop('PROTECTED_PATH_IN_SOURCE_DIFF')
        ignored = git('show',TARGET+':.dockerignore').stdout.splitlines()
        if not {'.env','data/','.secrets'}.issubset(set(ignored)):
            raise Stop('DOCKERIGNORE_GUARD_MISSING')
        report['stage'] = 'SWITCH_SOURCE'
        git('switch','--detach',TARGET)
        switched = True
        report['source_staged'] = True
        if git('status','--porcelain','--untracked-files=no').stdout.strip():
            raise Stop('STAGED_WORKTREE_NOT_CLEAN')
        report['stage'] = 'BUILD_IMAGE'
        with BUILD_LOG.open('w',encoding='utf-8') as log:
            built = command(['sudo','-n','docker','build','--pull=false','--tag',IMAGE,'.'],
                            cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=1800)
        report['build_log'] = str(BUILD_LOG)
        report['build_log_bytes'] = BUILD_LOG.stat().st_size
        if built.returncode:
            raise Stop('DOCKER_BUILD_FAILED')
        inspected = command(['sudo','-n','docker','image','inspect','--format',
                             '{"id":{{json .Id}},"user":{{json .Config.User}}}',IMAGE],
                            capture_output=True)
        if inspected.returncode:
            raise Stop('BUILT_IMAGE_INSPECT_FAILED')
        image = json.loads(inspected.stdout)
        if image.get('user') != 'botuser' or not re.fullmatch(
                r'sha256:[0-9a-f]{64}',str(image.get('id'))):
            raise Stop('BUILT_IMAGE_IDENTITY_INVALID')
        verified_image = True
        report['image'] = image
        report['image_built'] = True
        report['engines_after'] = engines()
        report['available_bytes_after'] = os.statvfs(ROOT/'data').f_bavail * os.statvfs(ROOT/'data').f_frsize
        report['status'] = 'STAGED_IMAGE_READY_REVIEW'
        report['stage'] = 'DONE'
    except Stop as exc:
        report.update(status='STOPPED', reason=str(exc))
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError) as exc:
        report.update(status='STOPPED', reason=type(exc).__name__)
        if isinstance(exc,OSError):
            report['os_errno'] = exc.errno
    finally:
        if switched and not verified_image:
            rollback = git('switch','testing',check=False)
            report['rollback_performed'] = rollback.returncode == 0
            report['source_staged'] = not report['rollback_performed']
        report['completed_at'] = datetime.now(timezone.utc).isoformat()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage-build',action='store_true',required=True)
    parser.parse_args(argv)
    output = json.dumps(stage_and_build(),ensure_ascii=False,indent=2,allow_nan=False)
    print(output,flush=True)
    print('\033]52;c;'+base64.b64encode(output.encode()).decode()+'\a',end='',flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
