"""Preopen compaction preserves actual canonical values, clocks and safety."""
import gzip
import hashlib
import json
import math

import pytest

from rc6_dynamic_universe.common import digest
from rc6_shadow_runtime.persistence import EvidenceFiles, FrozenJSONDict, streamed_digest


@pytest.mark.parametrize('seed', range(20))
def test_compact_read_preserves_full_canonical_bytes_and_shares_only_exact_metrics(tmp_path, seed):
    metric={'status':'NO_VERIFICADO','value':None,'samples':seed,'unit':'ñ🚀'}
    payload={'as_of':'2026-10-02T20:00:00+00:00','rows':[
        {'identity':[str(n),'CEDEARS','BYMA','ARS' if n%2 else 'USD_MEP','A-24HS'],
         'metric':dict(metric),'dynamic':{'source_at':str(seed),'price':n}}
        for n in range(300)]}
    files=EvidenceFiles(tmp_path/'shadow')
    name='preopen-2026-10-05.json.gz'
    with files:files.write(name,payload)
    original=files.read(name)
    compact=files.read(name,compact_preopen=True)
    encode=lambda x:json.dumps(x,sort_keys=True,separators=(',',':'),default=str,allow_nan=False).encode()
    assert encode(original)==encode(compact)==encode(payload)
    assert streamed_digest(compact)==digest(original)==hashlib.sha256(encode(payload)).hexdigest()
    assert compact['rows'][0]['metric'] is compact['rows'][-1]['metric']
    assert compact['rows'][0]['dynamic'] is not compact['rows'][-1]['dynamic']
    with pytest.raises(TypeError,match='IMMUTABLE'):
        compact['rows'][0]['metric']['value']=1


def test_typed_scalar_interning_preserves_bool_int_float_and_signed_zero(tmp_path):
    payload={'metrics':[{'status':'TEST','value':v} for v in (False,0,0.0,-0.0,True,1,1.0)]}
    files=EvidenceFiles(tmp_path/'shadow');name='preopen-2026-10-05.json.gz'
    with files:files.write(name,payload)
    metrics=files.read(name,compact_preopen=True)['metrics']
    assert len({id(x) for x in metrics})==7
    assert [type(x['value']) for x in metrics]==[bool,int,float,float,bool,int,float]
    assert math.copysign(1,metrics[2]['value'])==1
    assert math.copysign(1,metrics[3]['value'])==-1


def test_no_cross_read_cache_and_tampering_still_revokes_the_preopen(tmp_path):
    files=EvidenceFiles(tmp_path/'shadow');name='preopen-2026-10-05.json.gz'
    payload={'metric':{'status':'NO_VERIFICADO','value':None}}
    with files:files.write(name,payload)
    first=files.read(name,compact_preopen=True)
    dict.__setitem__(first['metric'],'value','bypassed normal immutable API')
    # No cached digest authority: deliberately bypassed mutation is still hashed.
    assert streamed_digest(first)!=digest(payload)
    assert files.read(name,compact_preopen=True)==payload
    path=files.root/name;envelope=json.loads(gzip.decompress(path.read_bytes()))
    envelope['payload']['metric']['value']='corrupt bytes'
    path.write_bytes(gzip.compress(json.dumps(envelope).encode()))
    with pytest.raises(ValueError,match='DIGEST_MISMATCH'):
        files.read(name,compact_preopen=True)


def test_compaction_cannot_change_other_file_roles_or_intern_nested_mutable_data(tmp_path):
    files=EvidenceFiles(tmp_path/'shadow')
    payload={'a':{'status':'X','value':{'mutable':1}},'b':{'status':'X','value':{'mutable':1}}}
    with files:
        files.write('status.json',payload)
        files.write('preopen-2026-10-05.json.gz',payload)
    with pytest.raises(ValueError,match='COMPACT_PREOPEN_ROLE_REQUIRED'):
        files.read('status.json',compact_preopen=True)
    value=files.read('preopen-2026-10-05.json.gz',compact_preopen=True)
    assert type(value['a']) is dict and value['a'] is not value['b']
