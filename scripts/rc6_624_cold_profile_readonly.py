"""Bounded read-only CPU/I/O profile of the failed immutable COLD input.

Runs inside the already deployed observer image; never calls drain, writes a
report, reads credentials, or changes the existing FAILED queue.
"""
import hashlib
import json
import os
from pathlib import Path
import resource
import time

from rc6_shadow_runtime import cold_audit

SNAPSHOT = '89e7f81dbc9b4a628bdc30edf86a17b0'
ROOT = Path('/app/data/paper_v17/artifacts/observer_v17.db/dynamic-shadow')


def measure(call):
    start, cpu = time.monotonic(), time.process_time()
    result = call()
    return result, {'wall_seconds': round(time.monotonic()-start, 6),
                    'cpu_seconds': round(time.process_time()-cpu, 6)}


def main():
    os.nice(10)
    resource.setrlimit(resource.RLIMIT_CPU, (25, 26))
    result = {'schema': 'rc6.issue624.cold-readonly-profile.v1',
              'read_only': True, 'ppi_watch_accessed': False,
              'snapshot_id': SNAPSHOT, 'phases': {}}
    files = cold_audit.p.EvidenceFiles(ROOT)
    with cold_audit.Directory(files) as directory:
        manifest = directory.read('manifest-'+SNAPSHOT+'.json')
        queue = directory.read('queue.json')
        receipt = directory.read('receipt-'+SNAPSHOT+'.json')
        assert manifest['snapshot_id'] == SNAPSHOT
        assert queue['state'] == 'FAILED' and queue['snapshot_id'] == SNAPSHOT
        assert receipt['state'] == 'FAILED' and receipt['snapshot_id'] == SNAPSHOT
        cold_audit._pack_identity(directory, manifest)
        with directory.open(manifest['pack']) as stream:
            pack_sha, profile = measure(lambda: cold_audit._hash(stream))
        assert pack_sha == manifest['pack_sha256']
        result['phases']['pack_sha256']=profile
        # Instrument the existing code path without changing its bytes or
        # disabling any validation. This process exits after the one scan.
        inner={}
        def instrument(label, original):
            def call(*args, **kwargs):
                start, cpu=time.monotonic(),time.process_time()
                try:return original(*args, **kwargs)
                finally:
                    row=inner.setdefault(label,{'calls':0,'wall_seconds':0.,'cpu_seconds':0.})
                    row['calls']+=1
                    row['wall_seconds']+=time.monotonic()-start
                    row['cpu_seconds']+=time.process_time()-cpu
            return call
        old_hash=cold_audit._hash
        cold_audit._hash=instrument('segment_sha256',old_hash)
        old_native=cold_audit._native
        class Native:
            def snapshot_context(self,*args):return old_native().snapshot_context(*args)
            def snapshot_decode(self,*args):return instrumented_decode(*args)
        instrumented_decode=instrument('snapshot_decode',old_native().snapshot_decode)
        cold_audit._native=lambda:Native()
        old_encoder=cold_audit.ENCODER
        class Encoder:
            encode=staticmethod(instrument('canonical_encode',old_encoder.encode))
        cold_audit.ENCODER=Encoder()
        def decode():
            digest=hashlib.sha256();size=0
            for part in cold_audit._expand(directory, manifest['pack'],
                                            manifest['root'], manifest['segments']):
                digest.update(part);size+=len(part)
                if size>512*1024**2:raise ValueError('PROFILE_RAW_BOUND')
            return {'raw_bytes':size,'raw_sha256':digest.hexdigest()}
        expanded, profile=measure(decode)
        result['phases']['verified_expand_and_canonical_encode']=profile
        result['phases']['expansion_components']={key:{k:round(v,6) if isinstance(v,float) else v
                                                   for k,v in row.items()} for key,row in inner.items()}
        result['expanded']=expanded
        result['pack_bytes']=manifest['pack_bytes']
        result['deadline_at']=manifest['deadline_at']
        result['failed_at']=receipt['failed_at']
        result['state_after']={
            'queue':directory.read('queue.json')['state'],
            'receipt':directory.read('receipt-'+SNAPSHOT+'.json')['state']}
    result['status']='OBSERVED'
    print(json.dumps(result,sort_keys=True,separators=(',',':')))


if __name__=='__main__':main()
