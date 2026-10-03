#!/usr/bin/env python3
"""Validate frozen canonical dashboard binds, not a blanket /app exception.

Only the read-only probe contract changes. Artifact/image/trading validation and
canonical recovery tail stay byte-identical. No mount or container is modified.
"""
import inspect
from pathlib import Path
import sys
import rc6_deploy_validation_resume as resume

BASE_MODULE_BLOB = 'f12312b31ceee4f7fc62933e495ba82b5d4bae2b'
OLD = ''' if name!='porota_critical_approval_rc6':
  for mount in info.get('Mounts',[]):
   dest=mount['Destination']
   check(not (dest=='/app' or (dest.startswith('/app/') and not (dest=='/app/data' or dest.startswith('/app/data/')))),'APP_SOURCE_MOUNT')'''
NEW = ''' if name!='porota_critical_approval_rc6':
  import ast
  mode_ast=ast.parse((root/'porota_mode_manager.py').read_text())
  dashboard_fn=next(n for n in mode_ast.body if isinstance(n,ast.FunctionDef) and n.name=='start_dashboard')
  source_assign=next(n for n in dashboard_fn.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='runtime_sources' for t in n.targets))
  allowed=ast.literal_eval(source_assign.value)
  expected_files={item['path']:item['sha256'] for item in manifest['files']}
  verified=validate_mounts(info.get('Mounts',[]),name,str(root),expected_files,allowed)
  if verified:
   hashes=cmd(['docker','exec',name,'sha256sum',*['/app/'+filename for filename in verified]])
   actual={line.split()[-1]:line.split()[0] for line in hashes.splitlines()}
   check(actual=={'/app/'+filename:expected_files[filename] for filename in verified},'CONTAINER_MOUNT_HASH_DRIFT')
  print('RESUME_CANONICAL_MOUNTS='+json.dumps({'container':name,'verified_source_files':verified,'status':'GREEN'}),file=sys.stderr)'''


def validate_mounts(mounts, name, root, expected_files, allowed):
    from pathlib import PurePosixPath
    def require(value, reason):
        if not value:
            raise RuntimeError(reason)
    require(isinstance(allowed, (tuple, list)) and len(allowed) == 18 and len(set(allowed)) == 18,
            'CANONICAL_MOUNT_LIST_INVALID')
    require(all(isinstance(p, str) and PurePosixPath(p).name == p and p.endswith('.py')
                and p in expected_files for p in allowed), 'CANONICAL_MOUNT_UNMANIFESTED')
    observed = set(); data = 0
    for mount in mounts:
        dest = mount.get('Destination', '')
        require(PurePosixPath(dest).is_absolute() and '..' not in PurePosixPath(dest).parts, 'MOUNT_PATH_INVALID')
        if dest != '/app' and not dest.startswith('/app/'):
            continue
        if dest == '/app/data':
            require(mount.get('Type') == 'bind' and mount.get('Source') == root + '/data', 'DATA_MOUNT_DRIFT')
            data += 1
            continue
        filename = dest[len('/app/'):]
        require(name == 'porota_production_dashboard' and filename in allowed,
                'UNEXPECTED_APP_SOURCE_MOUNT:' + dest)
        require(mount.get('Type') == 'bind' and mount.get('Source') == root + '/' + filename
                and mount.get('RW') is False, 'SOURCE_MOUNT_NOT_EXACT_READONLY:' + dest)
        require(filename not in observed, 'DUPLICATE_SOURCE_MOUNT')
        observed.add(filename)
    require(data == 1, 'DATA_MOUNT_NOT_UNIQUE')
    require(observed == (set(allowed) if name == 'porota_production_dashboard' else set()),
            'CANONICAL_SOURCE_MOUNT_MISSING')
    return sorted(observed)


def updated_probe():
    resume.require(resume.git_blob(Path(resume.__file__).read_text()) == BASE_MODULE_BLOB,
                   'UNREVIEWED_RESUME_MODULE')
    resume.require(resume.REMOTE_PROBE.count(OLD) == 1, 'MOUNT_ANCHOR_NOT_UNIQUE')
    return inspect.getsource(validate_mounts) + '\n' + resume.REMOTE_PROBE.replace(OLD, NEW)


def main():
    resume.require(len(sys.argv) > 1 and sys.argv[1] == 'finalize', 'FINALIZE_ONLY')
    resume.REMOTE_PROBE = updated_probe()
    sys.excepthook = lambda t, e, b: (print(getattr(e, 'stderr', '') or '', file=sys.stderr),
                                     sys.__excepthook__(t, e, b))
    resume.main()


if __name__ == '__main__':
    main()
