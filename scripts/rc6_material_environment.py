"""Create one fresh normal PRODUCT venv; refuse every existing namespace."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import stat
import venv

FIELDS=('st_dev','st_ino','st_uid','st_gid','st_mode','st_nlink','st_size','st_blocks','st_atime_ns','st_mtime_ns','st_ctime_ns')
def need(value,reason):
    if not value:raise ValueError(reason)
def identity(i):return {k:getattr(i,k) for k in FIELDS}
def mount_id(fd):
    with open('/proc/self/fdinfo/'+str(fd)) as stream:
        rows=[line.split(':',1)[1].strip() for line in stream if line.startswith('mnt_id:')]
    need(len(rows)==1 and rows[0].isdigit(),'FRESH_VENV_MOUNT_ID_REQUIRED')
    return int(rows[0])
def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--owner-uid',type=int,required=True)
    p.add_argument('--python-version',choices=('3.11.16','3.12.14'),required=True);p.add_argument('--receipt',type=Path,required=True)
    a=p.parse_args();os.umask(0o022)
    need(os.getuid()==os.geteuid()==a.owner_uid and a.owner_uid>0 and sys_platform_linux(),'VENV_EXPLICIT_REAL_OWNER_REQUIRED')
    need(platform.python_version()==a.python_version,'VENV_EXACT_INTERPRETER_REQUIRED')
    root=Path(os.path.abspath(a.root))
    need(not any(q.is_symlink() for q in (root,*root.parents)) and not os.path.lexists(root),'VENV_FRESH_NOFOLLOW_ROOT_REQUIRED')
    parent=root.parent.lstat();need(parent.st_uid==a.owner_uid and stat.S_IMODE(parent.st_mode)==0o700,'VENV_NEW_PRIVATE_PARENT700_REQUIRED')
    os.mkdir(root,0o755);fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        before=os.fstat(fd);mnt=mount_id(fd)
        need(before.st_uid==a.owner_uid and stat.S_IMODE(before.st_mode)==0o755 and before.st_dev==parent.st_dev,'VENV_NEW_DIRECTORY_IDENTITY_REQUIRED')
        venv.EnvBuilder(symlinks=False,with_pip=True).create(root)
        after=os.stat(root,follow_symlinks=False)
        need((after.st_dev,after.st_ino,after.st_uid,after.st_mode)==(before.st_dev,before.st_ino,before.st_uid,before.st_mode),'VENV_FRESH_DIRECTORY_REBOUND')
        lib=os.stat('lib',dir_fd=fd,follow_symlinks=False)
        need(stat.S_ISDIR(lib.st_mode) and lib.st_uid==a.owner_uid and lib.st_dev==before.st_dev,'FRESH_VENV_NATIVE_LIB_REQUIRED')
        link=os.stat('lib64',dir_fd=fd,follow_symlinks=False)
        need(stat.S_ISLNK(link.st_mode) and link.st_uid==a.owner_uid and link.st_dev==before.st_dev and link.st_nlink==1
             and os.readlink('lib64',dir_fd=fd)=='lib','FRESH_VENV_ONLY_NATIVE_LIB64_ALIAS_ALLOWED')
        link_fd=os.open('lib64',os.O_PATH|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
        lib_fd=os.open('lib',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
        try:
            need(mount_id(link_fd)==mnt==mount_id(lib_fd),'FRESH_NATIVE_LIB64_MOUNT_REBOUND')
            need(identity(os.fstat(link_fd))==identity(os.stat('lib64',dir_fd=fd,follow_symlinks=False)),'FRESH_NATIVE_LIB64_IDENTITY_REBOUND')
            os.unlink('lib64',dir_fd=fd) # Only this just-created native alias, after proof.
        finally:os.close(link_fd);os.close(lib_fd)
        need(not os.path.lexists(root/'lib64'),'FRESH_NATIVE_LIB64_REMOVAL_NOT_PROVED')
    finally:os.close(fd)
    report={'schema':'rc6.material-product-new-venv.v1','owner_uid':a.owner_uid,'python':platform.python_version(),
        'root':str(root),'new_directory_identity_before':identity(before),'native_lib64_before':identity(link),
        'native_lib64_target':'lib','native_alias_removed_only_at_creation':True,'existing_resources_modified':False,
        'PRODUCT157_claim':'NOT_INSTALLED_YET','real_orders_sent':0}
    wire=(json.dumps(report,sort_keys=True,separators=(',',':'))+'\n').encode()
    rf=os.open(a.receipt,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(rf,'wb') as stream:stream.write(wire);stream.flush();os.fsync(stream.fileno())
    return 0
def sys_platform_linux():
    import sys
    return sys.platform=='linux'
if __name__=='__main__':raise SystemExit(main())
