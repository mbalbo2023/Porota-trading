"""Preinstalación v17: sólo metadatos del host y Docker inspect, sin escrituras."""
import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import subprocess

VERSION = 'v17-preinstall-1'
ROOT = Path('/opt/porota-trading')
ENGINES = ('porota_trading_bot', 'porota_production_observer')


class Stop(Exception):
    pass


def engines():
    template = ('{"name":{{json .Name}},"running":{{json .State.Running}},'
                '"restart":{{json .HostConfig.RestartPolicy.Name}},'
                '"image":{{json .Image}},"user":{{json .Config.User}}}')
    result = subprocess.run(['sudo', '-n', 'docker', 'inspect', '--type', 'container',
                             '--format', template, *ENGINES],
                            capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise Stop('DOCKER_INSPECT_FAILED')
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    if (len(rows) != 2 or any(not isinstance(r, dict) for r in rows)
            or {r.get('name') for r in rows} != {'/'+name for name in ENGINES}):
        raise Stop('UNEXPECTED_ENGINE_METADATA')
    if any(r.get('running') is not False or r.get('restart') != 'no' for r in rows):
        raise Stop('ENGINES_MUST_REMAIN_STOPPED_NO_RESTART')
    if any(r.get('user') != 'botuser' or not re.fullmatch(r'sha256:[0-9a-f]{64}', str(r.get('image'))) for r in rows):
        raise Stop('ENGINE_INSTALLATION_CHANGED')
    # Sólo los campos solicitados; no se serializa ninguna otra configuración.
    return [{key:r[key] for key in ('name','running','restart','image','user')} for r in rows]


def metadata(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {'state':'MISSING'}
    kind = ('SYMLINK' if stat.S_ISLNK(info.st_mode) else
            'DIRECTORY' if stat.S_ISDIR(info.st_mode) else
            'FILE' if stat.S_ISREG(info.st_mode) else 'OTHER')
    return {'state':'PRESENT', 'kind':kind, 'uid':info.st_uid, 'gid':info.st_gid,
            'mode':format(stat.S_IMODE(info.st_mode), '04o'), 'bytes':info.st_size,
            'links':info.st_nlink}


def collect():
    report = {'diagnostic':VERSION, 'generated_at':datetime.now(timezone.utc).isoformat(),
              'status':'NOT_STARTED', 'stage':'ENGINES',
              'scope':'Sólo metadatos de rutas fijas y Docker inspect; sin bases, archivos de configuración o APIs.',
              'installation_performed':False, 'database_opened':False,
              'files_created':False, 'permissions_changed':False,
              'engines_started':False, 'promotion_allowed':False,
              'paths':{}, 'limits':[
                  'No prueba escritura como botuser ni mide permisos ACL o mapeos de UID de Docker.',
                  'Espacio del filesystem de data; no certifica espacio suficiente para build o rollback.',
                  'La existencia de la base nueva requiere revisión; no se abre, adopta ni reemplaza.',
                  'Fotografía de metadatos, no bloqueo de cambios externos ni autorización de instalación.']}
    try:
        report['engines'] = engines()
        report['stage'] = 'FIXED_PATH_METADATA'
        paths = {'project':ROOT, 'data':ROOT/'data',
                 'new_directory':ROOT/'data/paper_v17',
                 'new_database':ROOT/'data/paper_v17/observer_v17.db'}
        for label, path in paths.items():
            row = metadata(path)
            report['paths'][label] = dict(path=str(path), **row)
            if row['state'] == 'PRESENT' and row['kind'] in {'SYMLINK','OTHER'}:
                raise Stop('UNEXPECTED_PATH_KIND')
            if label in {'project','data'} and (row['state'] != 'PRESENT' or row['kind'] != 'DIRECTORY'):
                raise Stop('REQUIRED_DIRECTORY_MISSING_OR_INVALID')
            if label == 'new_directory' and row['state'] == 'PRESENT' and row['kind'] != 'DIRECTORY':
                raise Stop('NEW_DIRECTORY_PATH_OCCUPIED')
            if label == 'new_database' and row['state'] == 'PRESENT' and row['kind'] != 'FILE':
                raise Stop('NEW_DATABASE_PATH_OCCUPIED')
        report['stage'] = 'DATA_FILESYSTEM'
        fs = os.statvfs(paths['data'])
        report['data_filesystem'] = {'total_bytes':fs.f_blocks*fs.f_frsize,
                                    'available_bytes':fs.f_bavail*fs.f_frsize,
                                    'available_inodes':fs.f_favail}
        report['caller'] = {'effective_uid':os.geteuid(), 'effective_gid':os.getegid(),
                            'data_write_search_access':os.access(paths['data'], os.W_OK|os.X_OK, effective_ids=True)}
        report['status'] = ('OBSERVED_EXISTING_V17_PATH_REVIEW'
                            if report['paths']['new_database']['state'] == 'PRESENT'
                            else 'COMPLETED_HOST_METADATA')
        report['stage'] = 'DONE'
    except Stop as exc:
        report.update(status='STOPPED', reason=str(exc))
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError) as exc:
        report.update(status='STOPPED', reason=type(exc).__name__)
        if isinstance(exc, OSError):
            report['os_errno'] = exc.errno
    report['completed_at'] = datetime.now(timezone.utc).isoformat()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', action='store_true', required=True)
    parser.parse_args(argv)
    output = json.dumps(collect(), ensure_ascii=False, indent=2, allow_nan=False)
    print(output, flush=True)
    print('\033]52;c;' + base64.b64encode(output.encode()).decode() + '\a', end='', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
