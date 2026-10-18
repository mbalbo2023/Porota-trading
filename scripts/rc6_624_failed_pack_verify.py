"""Verify one original failed pack without printing or replaying its contents."""
import hashlib
import json
from pathlib import Path
import sys
import tarfile

IDENT='89e7f81dbc9b4a628bdc30edf86a17b0'
EXPECTED={'queue.json',IDENT+'.pack','manifest-'+IDENT+'.json','receipt-'+IDENT+'.json'}
SENSITIVE=(b'-----BEGIN PRIVATE KEY',b'client_secret',b'"access_token":',
           b'"password":',b'"api_key":',b'Bearer ',b'ghp_')


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),
        default=str,allow_nan=False,ensure_ascii=True).encode('ascii')).hexdigest()


def envelope(stream):
    raw=stream.read(256*1024+1)
    if len(raw)>256*1024:raise ValueError('ORIGINAL_ENVELOPE_BOUND')
    wrapper=json.loads(raw)
    if (type(wrapper) is not dict or set(wrapper)!={'payload','sha256'} or
            type(wrapper['payload']) is not dict or
            digest(wrapper['payload'])!=wrapper['sha256']):
        raise ValueError('ORIGINAL_ENVELOPE_HASH')
    return wrapper['payload']


def verify(source,output):
    with tarfile.open(source,'r:') as tar:
        members=tar.getmembers()
        if {m.name for m in members}!=EXPECTED or len(members)!=4 or any(
                not m.isfile() or m.name.startswith('/') or '..' in Path(m.name).parts for m in members):
            raise ValueError('ORIGINAL_TAR_MEMBERS')
        manifest=envelope(tar.extractfile('manifest-'+IDENT+'.json'))
        queue=envelope(tar.extractfile('queue.json'))
        receipt=envelope(tar.extractfile('receipt-'+IDENT+'.json'))
        if (manifest.get('snapshot_id')!=IDENT or manifest.get('pack')!=IDENT+'.pack' or
                queue.get('snapshot_id')!=IDENT or queue.get('state')!='FAILED' or
                receipt.get('snapshot_id')!=IDENT or receipt.get('state')!='FAILED' or
                receipt.get('error_class')!='ValueError' or
                queue.get('manifest_sha256')!=digest(manifest) or
                receipt.get('manifest_sha256')!=digest(manifest) or
                manifest.get('deadline_at')!=1792341299.9241242 or
                not 0<=receipt.get('failed_at',0)-manifest['deadline_at']<=.01):
            raise ValueError('ORIGINAL_FAILED_BINDING')
        size=0;h=hashlib.sha256();tail=b''
        with tar.extractfile(IDENT+'.pack') as pack:
            while chunk:=pack.read(1024*1024):
                value=tail+chunk
                if any(marker.lower() in value.lower() for marker in SENSITIVE):
                    raise ValueError('ORIGINAL_PACK_SENSITIVE_PATTERN_REQUIRES_REVIEW')
                tail=value[-max(map(len,SENSITIVE)):]
                h.update(chunk);size+=len(chunk)
        if size!=manifest['pack_bytes'] or size!=110327496 or h.hexdigest()!=manifest['pack_sha256']:
            raise ValueError('ORIGINAL_PACK_HASH')
    t=hashlib.sha256()
    with source.open('rb') as stream:
        while chunk:=stream.read(1024*1024):t.update(chunk)
    result={'schema':'rc6.issue624.original-failed-pack-byte-verification.v1',
            'status':'GREEN','read_only':True,'ppi_watch_accessed':False,
            'snapshot_id':IDENT,'state':'FAILED','pack_bytes':size,
            'pack_sha256':h.hexdigest(),'manifest_sha256':digest(manifest),
            'deadline_at':manifest['deadline_at'],'failed_at':receipt['failed_at'],
            'tar_bytes':source.stat().st_size,'tar_sha256':t.hexdigest(),
            'secret_pattern_scan':'PASS_REVIEWED_SENTINELS'}
    output.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')
    print('RC6_624_ORIGINAL_FAILED_PACK_BYTES=GREEN|'+str(size)+'|'+h.hexdigest())


if __name__=='__main__':
    if len(sys.argv)!=3:raise SystemExit('source.tar output.json required')
    verify(Path(sys.argv[1]),Path(sys.argv[2]))
