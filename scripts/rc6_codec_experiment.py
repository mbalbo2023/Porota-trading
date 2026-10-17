"""Compare encoders on ONE synthetic full-cardinality report; no native acceptance."""
import gc, hashlib, importlib.util, inspect, json, os, pathlib, resource, statistics, time, zlib
from scripts.rc6_postdeploy_stress import FULL_CATALOG_SHAPE
from be_paper_engine import PaperStore
from bf_production_paper_observer import _support_schema
from cf_intraday_scalping import init_schema
from tests.test_rc6_shadow_runtime_wiring import PRE
from rc6_shadow_runtime import worker as runtime
from rc6_shadow_runtime import persistence as candidate

root=pathlib.Path("/work/codec");root.mkdir(parents=True,exist_ok=True)
store=PaperStore(str(root/"paper.db"));_support_schema(store);init_schema(store)
rows=[]
for family,(total,ready) in FULL_CATALOG_SHAPE.items():
    for n in range(total):
        rows.append((f"LOAD_{family}_{n:05d}",family,"BYMA",("ARS","USD_MEP","USD_CCL")[n%3],
            "A-24HS","SYNTHETIC","synthetic public-cardinality fixture",PRE.isoformat(),
            "codec-benchmark","AVAILABLE" if n<ready else "PAUSED_EXPLICIT",
            "READY_PAPER_SPOT" if n<ready else "NO_VERIFICADO","{}"))
with store.connect() as connection:
    connection.executemany("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",rows)
del rows
report=runtime.ShadowRuntime(store.path,evidence_root=root/"shadow",source_roots=[]).tick(PRE)
spec=importlib.util.spec_from_file_location("baseline_persistence","/app/rc6_shadow_runtime/persistence.py")
baseline=importlib.util.module_from_spec(spec);spec.loader.exec_module(baseline)
baseline.FrozenJSONDict=candidate.FrozenJSONDict
original_small=baseline._small_json_tree
original_fragments=inspect.getsource(baseline._canonical_fragments)
key_fragments=original_fragments.replace("    active = set()","    active = set()\n    escaped_keys = {}")
old="""                    key_object = encoder.encode({key: None})
                    yield key_object[1:-len(':null}')]"""
new="""                    if type(key) is str:
                        token = escaped_keys.get(key)
                        if token is None:
                            token = encoder.encode(key)
                            if len(token) <= 4096:
                                if len(escaped_keys) >= 512:escaped_keys.clear()
                                escaped_keys[key] = token
                        yield token
                    else:
                        key_object = encoder.encode({key: None})
                        yield key_object[1:-len(':null}')]"""
assert old in key_fragments
key_fragments=key_fragments.replace(old,new)

def measured_small_factory(flat=False):
    key_shapes,flat_shapes={},{}
    scalar={str,int,float,bool,type(None)}
    def flat_upper(values):
        kinds=tuple(map(type,values))
        shape=flat_shapes.get(kinds)
        if shape is None:
            if set(kinds)<=scalar:
                strings=tuple(i for i,t in enumerate(kinds) if t is str)
                integers=tuple(i for i,t in enumerate(kinds) if t is int)
                fixed=sum(64 if t is float else 5 if t in (bool,type(None)) else 0 for t in kinds)
                shape=(strings,integers,fixed)
            else:shape=False
            if len(flat_shapes)<512:flat_shapes[kinds]=shape
        if shape is False:return None
        strings,integers,fixed=shape
        get=values.__getitem__
        return (12*sum(map(len,map(get,strings)))+2*len(strings)
                +sum(map(int.bit_length,map(get,integers)))+3*len(integers)+fixed)
    def small(value,maximum=65536,nodes=4096):
        pending=[value]
        while pending:
            item=pending.pop();nodes-=1
            if nodes<0:return False
            kind=type(item)
            if kind is candidate.FrozenJSONDict:maximum-=len(item._canonical_json)
            elif kind is str:maximum-=len(json.encoder.encode_basestring_ascii(item))
            elif item is None or kind is bool:maximum-=5
            elif kind is int:maximum-=max(1,item.bit_length())+3
            elif kind is float:maximum-=64
            elif kind is dict:
                if len(item)>256:return False
                keys=tuple(item);types=tuple(map(type,keys));shape=(types,keys)
                bound=key_shapes.get(shape)
                if bound is None and all(t is str for t in types):
                    bound=(len(json.dumps(keys,separators=(",",":"),allow_nan=False))+len(keys)+1 if keys else 2)
                    if len(key_shapes)<1024:key_shapes[shape]=bound
                if bound is None:
                    maximum-=2+2*len(item)
                    for key in keys:
                        if type(key) is str:maximum-=len(json.encoder.encode_basestring_ascii(key))
                        else:pending.append(key);maximum-=2
                else:maximum-=bound
                values=tuple(item.values())
                upper=flat_upper(values) if flat else None
                if upper is not None and upper<=maximum:
                    maximum-=upper;nodes-=len(values)
                else:pending.extend(values)
            elif kind is list or kind is tuple:
                if len(item)>256:return False
                maximum-=2+len(item)
                upper=flat_upper(tuple(item)) if flat else None
                if upper is not None and upper<=maximum:
                    maximum-=upper;nodes-=len(item)
                else:pending.extend(item)
            else:return False
            if maximum<0 or nodes<0:return False
        return True
    return small

receipts=[];expected=None
for variant in ("baseline","keys","key_bounds","flat_bounds","flat_bounds","key_bounds","keys","baseline"):
    if variant=="baseline":
        baseline._small_json_tree=original_small
        exec(original_fragments,baseline.__dict__)
    else:
        baseline._small_json_tree=(original_small if variant=="keys"
                                  else measured_small_factory(flat=variant=="flat_bounds"))
        exec(key_fragments,baseline.__dict__)
    gc.collect()
    start=time.monotonic();cpu=time.process_time();size=0;compressed=0;h=hashlib.sha256()
    compressor=zlib.compressobj(1,wbits=31)
    encoder=json.JSONEncoder(sort_keys=True,separators=(",",":"),default=str,allow_nan=False)
    for block in baseline._canonical_chunks(encoder,report):
        size+=len(block);h.update(block);compressed+=len(compressor.compress(block))
    compressed+=len(compressor.flush())
    receipt={"variant":variant,"wall_seconds":round(time.monotonic()-start,3),
        "cpu_seconds":round(time.process_time()-cpu,3),"raw_bytes":size,
        "sha256":h.hexdigest(),"compressed_bytes":compressed,
        "peak_rss_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    assert size>100*1024**2 and receipt["peak_rss_kib"]<=512*1024
    if expected is None:expected=(size,h.hexdigest())
    assert expected==(size,h.hexdigest()),"CANONICAL_BYTES_CHANGED"
    receipts.append(receipt)
    print("RC6_CODEC_EXPERIMENT="+json.dumps(receipt,separators=(",",":")),flush=True)
summary={v:statistics.median(r["wall_seconds"] for r in receipts if r["variant"]==v)
         for v in {r["variant"] for r in receipts}}
result={"schema":"rc6.synthetic-codec-experiment.v1","source_sha":"db06f0a20787ffc4d9f1211b7f2f5fdf04557c14",
        "receipts":receipts,"median_wall_seconds":summary,"raw_bytes_and_hash_identical":True,
        "cpu_limit":.75,"memory_limit_mib":512,"native_acceptance":False,
        "provider_requests":0,"real_orders_sent":0}
pathlib.Path("/work/codec-experiment.json").write_text(json.dumps(result,indent=2)+"\n")
print("RC6_CODEC_MEDIANS="+json.dumps(summary,sort_keys=True,separators=(",",":")))
