from pathlib import Path
import hashlib
import json
import os
import stat

RAW = Path('/tmp/rc6-packed-capture-0b380438-157-raw')
SOURCE = Path('/workspace/rc6-packed-capture-0b380438-157-source')
files, modes, blobs = {}, {}, {}
for record in (RAW / 'git-tree.records').read_bytes().split(b'\0'):
    if not record:
        continue
    head, raw_name = record.split(b'\t', 1)
    mode, kind, blob = head.decode().split()
    if kind != 'blob' or mode not in ('100644', '100755'):
        raise ValueError('SOURCE_REGULAR_GIT_MODE_REQUIRED')
    name = raw_name.decode()
    path = SOURCE / name
    info = path.lstat()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME)
    try:
        parts = []
        while raw := os.read(fd, 1024**2):
            parts.append(raw)
        raw = b''.join(parts)
        if os.fstat(fd) != info or path.lstat() != info:
            raise ValueError('SOURCE_CHANGED_DURING_PIN')
    finally:
        os.close(fd)
    actual_blob = hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
    actual_mode = '100'+format(stat.S_IMODE(info.st_mode), '03o')
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or actual_mode != mode or actual_blob != blob:
        raise ValueError('SOURCE_GIT_BLOB_MODE_MISMATCH')
    files[name], modes[name], blobs[name] = hashlib.sha256(raw).hexdigest(), mode, blob
pin = dict(schema='rc6.whole-source-native-trial-pin.v2',
    source_sha='0b3804388d1071959d16a0f382827aaf018b6429',
    source_tree='191ff6b66a69fad2234db6c50ff743c91fa45c6f',
    files=files, modes=modes, blob_ids=blobs, overlay_count=0,
    tar_sha256=hashlib.sha256((RAW/'source.tar').read_bytes()).hexdigest())
(RAW/'source.index.json').write_text(json.dumps(pin, indent=2, sort_keys=True)+'\n')
print(json.dumps(dict(source_sha=pin['source_sha'],source_tree=pin['source_tree'],
    source_files=len(files), tar_sha256=pin['tar_sha256'], overlay_count=0), sort_keys=True))
