"""Lossless supplementary RAW staging, callable only after every native FIN.

No fixture/Data/venv traversal, Source export tar copy, or cleanup is performed.
"""
import hashlib
import os
from pathlib import Path
import re
import stat

FIELDS=('st_dev','st_ino','st_uid','st_gid','st_mode','st_nlink','st_size','st_blocks','st_atime_ns','st_mtime_ns','st_ctime_ns')
SOURCE_LIMIT=128*1024**2
JUNIT_LIMIT=16*1024**2
TOTAL_STAGE_LIMIT=2*1024**3
MAX_STAGE_FILES=4096
ALLOWED_ENDINGS=('.json','.xml','.log','.py','.source','.original','.diff','.txt','.raw','.png')

def require(ok,reason):
    if not ok:raise ValueError(reason)
def attributes(info):return {name:getattr(info,name) for name in FIELDS}
def safe_path(path):
    path=Path(os.path.abspath(path))
    require(not any(p.is_symlink() for p in (path,*path.parents)),'RAW_ALIAS_FORBIDDEN')
    return path

def bundle(*,namespace,output_root,groups,files,read,save_raw,save,source_sha,source_tree,gate):
    """The caller supplies only genuinely closed, selected producer namespaces."""
    namespace=safe_path(namespace);output=safe_path(output_root)
    require(not os.path.lexists(output) and output.parent==namespace,'NEW_RAW_STAGE_REQUIRED')
    require(namespace.lstat().st_uid==os.geteuid(),'OWN_RAW_NAMESPACE_REQUIRED')
    output.mkdir(mode=0o700)
    rows=[];total=0;skipped=[];seen=set();candidates=[]
    device=namespace.lstat().st_dev
    for label,root in groups:
        require(re.fullmatch('[a-zA-Z0-9_.-]+',label),'RAW_LITERAL_GROUP_REQUIRED')
        root=safe_path(root)
        require(root.is_relative_to(namespace),'RAW_GROUP_OUTSIDE_OWN_NAMESPACE')
        if not os.path.lexists(root):continue
        identity=root.lstat()
        require(stat.S_ISDIR(identity.st_mode) and identity.st_uid==os.geteuid()
                and identity.st_dev==device and not stat.S_IMODE(identity.st_mode)&0o022,'RAW_OWN_SAME_DISK_DIRECTORY_REQUIRED')
        fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_NOATIME|os.O_CLOEXEC)
        try:
            require(attributes(identity)==attributes(os.fstat(fd)),'RAW_DIRECTORY_REBOUND')
            for name in sorted(os.listdir(fd)):
                info=os.stat(name,dir_fd=fd,follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    skipped.append({'group':label,'name':name,'reason':'DIRECTORY_NOT_TRAVERSED'})
                    continue
                require(stat.S_ISREG(info.st_mode) and info.st_uid==os.geteuid()
                        and info.st_nlink==1 and info.st_dev==device,'RAW_ALIAS_SPECIAL_FOREIGN_OR_HARDLINK_FORBIDDEN')
                if name=='source.tar' or not name.endswith(ALLOWED_ENDINGS):
                    skipped.append({'group':label,'name':name,'reason':'NOT_SELECTED_PAYLOAD'})
                    continue
                candidates.append((label+'/'+name,root/name))
        finally:os.close(fd)
    for destination,path in files:
        path=safe_path(path)
        require(path.is_relative_to(namespace),'RAW_SELECTED_FILE_OUTSIDE_OWN_NAMESPACE')
        if os.path.lexists(path):candidates.append((destination,path))
    require(len(candidates)<=MAX_STAGE_FILES,'RAW_STAGE_FILE_BOUND')
    for destination,path in candidates:
        relative=Path(destination)
        require(not relative.is_absolute() and relative.parts and all(p not in ('.','..') for p in relative.parts)
                and all(re.fullmatch('[a-zA-Z0-9_.-]+',p) for p in relative.parts),'LITERAL_RAW_DESTINATION_REQUIRED')
        require(destination not in seen,'RAW_DESTINATION_DUPLICATED');seen.add(destination)
        before=path.lstat()
        require(stat.S_ISREG(before.st_mode) and before.st_uid==os.geteuid() and before.st_nlink==1
                and before.st_dev==device and not stat.S_IMODE(before.st_mode)&0o022,'RAW_SELECTED_FILE_NOT_OWN_REGULAR')
        maximum=JUNIT_LIMIT if path.suffix=='.xml' else SOURCE_LIMIT
        original=read(path,maximum=maximum)
        after=path.lstat();require(attributes(before)==attributes(after),'RAW_ORIGINAL_ALL11_DRIFT')
        total+=len(original);require(total<=TOTAL_STAGE_LIMIT,'RAW_STAGE_TOTAL_BOUND')
        target=output/relative
        cursor=output
        for part in relative.parts[:-1]:
            cursor=cursor/part
            if not cursor.exists():cursor.mkdir(mode=0o700)
            require(cursor.lstat().st_uid==os.geteuid() and stat.S_IMODE(cursor.lstat().st_mode)==0o700,'RAW_STAGE_PARENT700_REQUIRED')
        stored_sha=save_raw(target,original)
        stored=read(target,maximum=maximum)
        require(stored==original and hashlib.sha256(original).hexdigest()==stored_sha,'RAW_LOSSLESS_STORED_BYTES_MISMATCH')
        rows.append({'original_path':str(path),'stored_path':relative.as_posix(),'bytes':len(original),
            'original_sha256':stored_sha,'stored_sha256':hashlib.sha256(stored).hexdigest(),
            'original_stat_before':attributes(before),'original_stat_after':attributes(after),
            'stored_stat':attributes(target.lstat()),'encoding_transformed':False})
    manifest={'schema':'rc6.supplementary-owned-raw-lossless.v1','source_sha':source_sha,'source_tree':source_tree,
        'gate':gate,'files':rows,'file_count':len(rows),'bytes':total,'excluded_entries':skipped,
        'all_launched_native_FIN_required_by_caller':True,'source_limit_bytes':SOURCE_LIMIT,'junit_limit_bytes':JUNIT_LIMIT,
        'only_explicit_closed_namespaces_traversed_one_level':True,'Source_tar_DB_archive_venv_fixture_content_copied':False,
        'promotable_artifact':False,'artifact_binding_validated':False,'runtime_validated':False,'GLOBAL_CLEANUP_GREEN':False,
        'real_orders_sent':0}
    save(output/'raw-lossless.manifest.json',manifest)
    return {'payload_root':str(output),'manifest_path':str(output/'raw-lossless.manifest.json'),
            'file_count':len(rows),'bytes':total}
