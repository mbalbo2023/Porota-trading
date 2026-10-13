from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tarfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--sha', required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--raw', type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve(strict=True)
    source, raw = args.source.absolute(), args.raw.absolute()
    assert re.fullmatch('[0-9a-f]{40}', args.sha)
    assert not source.exists() and not raw.exists()
    assert not source.is_relative_to(repo) and not raw.is_relative_to(repo)
    assert not source.is_relative_to(raw) and not raw.is_relative_to(source)
    def git(*argv):
        return subprocess.check_output(['git', '--no-replace-objects', '-C', str(repo), *argv])
    assert not git('for-each-ref', '--format=%(refname)', 'refs/replace').strip()
    assert not git('status', '--porcelain').strip()
    assert git('rev-parse', 'HEAD').decode().strip() == args.sha
    commit = git('cat-file', 'commit', args.sha)
    assert hashlib.sha1(b'commit '+str(len(commit)).encode()+b'\0'+commit).hexdigest() == args.sha
    tree = commit.splitlines()[0].decode().removeprefix('tree ')
    assert re.fullmatch('[0-9a-f]{40}', tree)
    members = {}
    for record in git('ls-tree', '-r', '-z', '--full-tree', args.sha).split(b'\0'):
        if not record:
            continue
        metadata, name = record.split(b'\t', 1)
        mode, kind, blob = metadata.decode().split()
        name = name.decode()
        path = PurePosixPath(name)
        assert kind == 'blob' and mode in ('100644', '100755')
        assert not path.is_absolute() and '..' not in path.parts and str(path) == name
        assert name not in members
        members[name] = {'mode': mode, 'blob_id': blob}
    os.umask(0o022)
    raw.mkdir(mode=0o700)
    archive = raw/'source.tar'
    subprocess.run(['git', '--no-replace-objects', '-C', str(repo), 'archive', '--format=tar',
                    '--output='+str(archive), args.sha], check=True)
    with tarfile.open(archive, 'r:') as tar:
        files = [member for member in tar.getmembers() if member.isfile()]
        assert len(files) == len(members) and {member.name for member in files} == set(members)
        assert all(member.isfile() or member.isdir() for member in tar.getmembers())
        source.mkdir(mode=0o700)
        for member in tar.getmembers():
            path = PurePosixPath(member.name.rstrip('/'))
            assert not path.is_absolute() and '..' not in path.parts
        tar.extractall(source, filter='data')
    files, modes, blobs = {}, {}, {}
    for name, record in members.items():
        path = source/name
        info = path.lstat()
        assert stat.S_ISREG(info.st_mode) and info.st_nlink == 1
        assert stat.S_IMODE(info.st_mode) == int(record['mode'][-3:], 8), name
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME)
        try:
            sha256 = hashlib.sha256()
            git_sha1 = hashlib.sha1(b'blob '+str(info.st_size).encode()+b'\0')
            while block := os.read(fd, 1024*1024):
                sha256.update(block)
                git_sha1.update(block)
            assert os.fstat(fd) == info and path.lstat() == info, name
        finally:
            os.close(fd)
        assert git_sha1.hexdigest() == record['blob_id'], name
        files[name], modes[name], blobs[name] = sha256.hexdigest(), record['mode'], record['blob_id']
    assert {str(p.relative_to(source)) for p in source.rglob('*') if p.is_file()} == set(members)
    assert not git('for-each-ref', '--format=%(refname)', 'refs/replace').strip()
    assert not git('status', '--porcelain').strip()
    assert git('rev-parse', 'HEAD').decode().strip() == args.sha
    archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    index = {'schema': 'rc6.complete-archive-source-pin.v1', 'source_sha': args.sha, 'source_tree': tree,
             'overlay_count': 0, 'tar_sha256': archive_sha, 'files': files, 'modes': modes,
             'blob_ids': blobs, 'raw_git_commit_sha256': hashlib.sha256(commit).hexdigest(),
             'scope': 'ALL_COMMITTED_GIT_BLOBS_AND_MODES_NOT_EXECUTION_OR_ARTIFACT_PROOF'}
    index_path = raw/'source.index.json'
    index_path.write_text(json.dumps(index, sort_keys=True, indent=2)+'\n')
    print(json.dumps({'source_sha': args.sha, 'source_tree': tree, 'source_files': len(files),
                      'tar_sha256': archive_sha, 'source_index_sha256': hashlib.sha256(index_path.read_bytes()).hexdigest(),
                      'source': str(source), 'source_index': str(index_path), 'overlay_count': 0}))


if __name__ == '__main__':
    main()
