#!/usr/bin/env python3
"""Bind the A138 frozen files to the live GitHub artifact before host cleanup."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import stat
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

RUN_ID = 40589182858
ARTIFACT_ID = 12775190272
CANDIDATE = 'dd22a1b0761cc4f231813edb6b502d1dd21bb8de'
ARTIFACT_DIGEST = 'sha256:94346b9fd503d5472cba121aa7b58d1e44bbb0dafac95e1de28b0ec72a8ee02d'
ARTIFACT_SIZE = 299708996
FILES = {
    'porota-frozen-candidate.json':610,
    'porota-predeploy-image.tar.gz':278661835,
    'porota-deploy-bundle-v2.tgz':1701819,
    'porota-deploy-bundle-v2-manifest.json':91356,
    'porota-host-manifest-v2.json':33622,
    'porota-host-policy-v2.json':184,
}


def verify_metadata(run, payload, now=None):
    if (run.get('id')!=RUN_ID or run.get('head_sha')!=CANDIDATE or
        run.get('status')!='completed' or run.get('conclusion')!='success'):
        raise ValueError('PREDEPLOY_RUN_METADATA_RED')
    matches=[a for a in payload.get('artifacts',[]) if a.get('id')==ARTIFACT_ID]
    if len(matches)!=1:
        raise ValueError('EXACT_ARTIFACT_MISSING')
    a=matches[0]
    if (a.get('name')!='porota-predeploy-v2-'+CANDIDATE or
        a.get('digest')!=ARTIFACT_DIGEST or a.get('size_in_bytes')!=ARTIFACT_SIZE or
        a.get('expired') is not False or a.get('workflow_run',{}).get('id')!=RUN_ID or
        a.get('workflow_run',{}).get('head_sha')!=CANDIDATE):
        raise ValueError('EXACT_ARTIFACT_METADATA_RED')
    expires=datetime.fromisoformat(a['expires_at'].replace('Z','+00:00'))
    if expires.tzinfo is None or expires.timestamp()<=((now or time.time())+300):
        raise ValueError('ARTIFACT_EXPIRING_RED')
    return expires.isoformat()


def verify_zip(path):
    if path.stat().st_size!=ARTIFACT_SIZE:
        raise ValueError('ARTIFACT_ZIP_SIZE_RED')
    sha=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):
            sha.update(chunk)
    if 'sha256:'+sha.hexdigest()!=ARTIFACT_DIGEST:
        raise ValueError('ARTIFACT_ZIP_DIGEST_RED')
    out={}
    with zipfile.ZipFile(path) as z:
        if len(z.infolist())>5000:
            raise ValueError('ARTIFACT_ZIP_ENTRY_CAP')
        for info in z.infolist():
            p=PurePosixPath(info.filename)
            if (p.is_absolute() or '..' in p.parts or info.is_dir()):
                continue
            name=p.name
            if name not in FILES:
                continue
            if name in out or info.file_size!=FILES[name]:
                raise ValueError('ARTIFACT_MEMBER_COLLISION_OR_SIZE_RED')
            mode=info.external_attr>>16
            if stat.S_IFMT(mode) and not stat.S_ISREG(mode):
                raise ValueError('ARTIFACT_MEMBER_TYPE_RED')
            sha=hashlib.sha256()
            size=0
            with z.open(info) as f:
                while chunk:=f.read(1024*1024):
                    size+=len(chunk)
                    if size>FILES[name]:
                        raise ValueError('ARTIFACT_MEMBER_READ_CAP')
                    sha.update(chunk)
            if size!=FILES[name]:
                raise ValueError('ARTIFACT_MEMBER_SIZE_RED')
            out[name]=sha.hexdigest()
    if set(out)!=set(FILES):
        raise ValueError('ARTIFACT_FROZEN_MEMBER_SET_RED')
    return out


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--run-json',type=Path,required=True)
    ap.add_argument('--artifact-json',type=Path,required=True)
    ap.add_argument('--zip',type=Path,required=True)
    ap.add_argument('--base64-out',type=Path,required=True)
    args=ap.parse_args()
    expires=verify_metadata(json.loads(args.run_json.read_text()),
                            json.loads(args.artifact_json.read_text()))
    frozen=verify_zip(args.zip)
    encoded=base64.b64encode(json.dumps(frozen,sort_keys=True).encode('ascii'))
    fd=os.open(args.base64_out,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:
        f.write(encoded)
    print('RC6_A159_ARTIFACT=GREEN|id='+str(ARTIFACT_ID)+
          '|digest='+ARTIFACT_DIGEST+'|frozen_files='+str(len(frozen))+ '|expires_at='+expires)


if __name__=='__main__':
    main()
