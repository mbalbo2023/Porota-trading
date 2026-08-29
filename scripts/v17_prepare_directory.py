"""Prepara sólo el directorio PAPER v17, sin crear o abrir su base."""
import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import subprocess

VERSION = 'v17-prepare-directory-1'
ROOT = Path('/opt/porota-trading')
ENGINES = ('porota_trading_bot', 'porota_production_observer')
EXPECTED_ID = 1000
MIN_AVAILABLE = 1024 ** 3


class Stop(Exception):
    pass


def check_engines():
    template = ('{"name":{{json .Name}},"running":{{json .State.Running}},'
                '"restart":{{json .HostConfig.RestartPolicy.Name}},'
                '"image":{{json .Image}},"user":{{json .Config.User}}}')
    result = subprocess.run(['sudo', '-n', 'docker', 'inspect', '--type', 'container',
                             '--format', template, *ENGINES], capture_output=True,
                            text=True, timeout=20)
    if result.returncode:
        raise Stop('DOCKER_INSPECT_FAILED')
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    if len(rows) != 2 or {r.get('name') for r in rows} != {'/'+n for n in ENGINES}:
        raise Stop('UNEXPECTED_ENGINE_METADATA')
    if any(r.get('running') is not False or r.get('restart') != 'no' for r in rows):
        raise Stop('ENGINES_MUST_REMAIN_STOPPED_NO_RESTART')
    if any(r.get('user') != 'botuser' or
           not re.fullmatch(r'sha256:[0-9a-f]{64}', str(r.get('image'))) for r in rows):
        raise Stop('ENGINE_INSTALLATION_CHANGED')
    return [{k:r[k] for k in ('name','running','restart','image','user')} for r in rows]


def directory_metadata(path):
    info = path.lstat()
    return {'path':str(path), 'uid':info.st_uid, 'gid':info.st_gid,
            'mode':format(stat.S_IMODE(info.st_mode),'04o'), 'links':info.st_nlink}


def prepare():
    data = ROOT/'data'
    target = data/'paper_v17'
    database = target/'observer_v17.db'
    report = {'diagnostic':VERSION, 'generated_at':datetime.now(timezone.utc).isoformat(),
              'status':'NOT_STARTED', 'stage':'ENGINES', 'directory_created':False,
              'write_probe_created':False, 'write_probe_removed':False,
              'database_created':False, 'database_opened':False,
              'legacy_database_touched':False, 'engines_started':False,
              'promotion_allowed':False}
    created = False
    probe = target/'.v17_write_probe.tmp'
    try:
        report['engines'] = check_engines()
        report['stage'] = 'HOST_IDENTITY'
        if os.geteuid() != EXPECTED_ID or os.getegid() != EXPECTED_ID:
            raise Stop('CALLER_MUST_BE_UID_GID_1000')
        for path in (ROOT, data):
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise Stop('REQUIRED_DIRECTORY_INVALID')
        data_info = data.lstat()
        if data_info.st_uid != EXPECTED_ID or data_info.st_gid != EXPECTED_ID:
            raise Stop('DATA_OWNER_CHANGED')
        fs = os.statvfs(data)
        available = fs.f_bavail * fs.f_frsize
        report['available_bytes'] = available
        if available < MIN_AVAILABLE:
            raise Stop('INSUFFICIENT_FREE_SPACE')
        report['stage'] = 'TARGET'
        if database.exists() or database.is_symlink():
            raise Stop('NEW_DATABASE_ALREADY_EXISTS')
        if target.is_symlink():
            raise Stop('TARGET_IS_SYMLINK')
        if target.exists():
            if not target.is_dir():
                raise Stop('TARGET_NOT_DIRECTORY')
            if target.lstat().st_uid != EXPECTED_ID or target.lstat().st_gid != EXPECTED_ID:
                raise Stop('TARGET_OWNER_INVALID')
            if stat.S_IMODE(target.lstat().st_mode) != 0o750:
                raise Stop('TARGET_MODE_INVALID')
            if any(target.iterdir()):
                raise Stop('TARGET_NOT_EMPTY')
        else:
            target.mkdir(mode=0o750)
            os.chmod(target, 0o750)
            created = True
            report['directory_created'] = True
        report['stage'] = 'WRITE_PROBE'
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, 'O_NOFOLLOW'):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(probe, flags, 0o600)
        report['write_probe_created'] = True
        try:
            os.write(descriptor, b'porota-v17-write-probe\n')
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        probe.unlink()
        report['write_probe_removed'] = True
        report['directory'] = directory_metadata(target)
        report['status'] = 'PREPARED' if created else 'ALREADY_PREPARED'
        report['stage'] = 'DONE'
    except Stop as exc:
        report.update(status='STOPPED', reason=str(exc))
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError) as exc:
        report.update(status='STOPPED', reason=type(exc).__name__)
        if isinstance(exc, OSError):
            report['os_errno'] = exc.errno
    finally:
        if probe.exists() and not probe.is_symlink():
            try:
                probe.unlink()
                report['write_probe_removed'] = True
            except OSError:
                pass
        report['completed_at'] = datetime.now(timezone.utc).isoformat()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', action='store_true', required=True)
    parser.parse_args(argv)
    output = json.dumps(prepare(), ensure_ascii=False, indent=2, allow_nan=False)
    print(output, flush=True)
    print('\033]52;c;' + base64.b64encode(output.encode()).decode() + '\a', end='', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
