"""Compare encoders on ONE synthetic full-cardinality report; no native acceptance."""
import gc, hashlib, importlib.util, inspect, json, os, pathlib, resource, statistics, time, zlib
from scripts.rc6_postdeploy_stress import FULL_CATALOG_SHAPE
from be_paper_engine import PaperStore
from bf_production_paper_observer import _support_schema
from cf_intraday_scalping import init_schema
from tests.test_rc6_shadow_runtime_wiring import PRE
from rc6_shadow_runtime import worker as runtime
from rc6_shadow_runtime import persistence as candidate

root=pathlib.Path("/work/shared-list-cache");root.mkdir(parents=True,exist_ok=True)
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

import gzip,sys
original_fragments=candidate._canonical_fragments
encoder=json.JSONEncoder(sort_keys=True,separators=(",",":"),default=str,allow_nan=False)
groups={}
for engine in report.get("frozen",{}).values():
    rows=engine.get("payload",{}).get("rows") if isinstance(engine,dict) else None
    if isinstance(rows,list):groups.setdefault(id(rows),{"value":rows,"occurrences":0})["occurrences"]+=1
targets={identity for identity,item in groups.items() if item["occurrences"]>=2 and len(item["value"])>256}
print("RC6_FROZEN_ROW_ALIAS_SHAPE="+json.dumps(
    [{"rows":len(item["value"]),"occurrences":item["occurrences"]} for item in groups.values()],
    sort_keys=True,separators=(",",":")),flush=True)
assert targets,"NO_SHARED_FROZEN_ROW_LIST_IN_FULL_REPORT"
def cached_fragments(limit,stats):
    def produce(encoder,payload):
        active=set();cache={};used=0;capturing=False
        def fragments(value):
            nonlocal used,capturing
            identity=id(value)
            hit=cache.get(identity)
            if hit is not None:
                assert hit[0] is value
                stats["hits"]+=1;stats["reused_characters"]+=hit[2]
                yield from hit[1]
                return
            eligible=identity in targets and not capturing
            if not eligible:
                yield from fresh(value)
                return
            saved=[];charge=0;characters=0;capturing=True;fits=True
            try:
                for fragment in fresh(value):
                    if fits:
                        charge+=sys.getsizeof(fragment)+16
                        characters+=len(fragment)
                        if used+charge+512<=limit:saved.append(fragment)
                        else:saved.clear();fits=False;stats["budget_rejections"]+=1
                    yield fragment
            finally:
                capturing=False
            if fits:
                cache[identity]=(value,tuple(saved),characters)
                used+=charge+512
                stats["entries"]+=1;stats["charged_bytes"]=used
        def fresh(value):
            container=isinstance(value,(dict,list,tuple))
            if not container or candidate._small_json_tree(value):
                yield encoder.encode(value);return
            if id(value) in active:raise ValueError("Circular reference detected")
            active.add(id(value))
            try:
                if isinstance(value,dict):
                    yield "{"
                    for index,key in enumerate(sorted(value)):
                        if index:yield ","
                        encoded=encoder.encode({key:None})
                        yield encoded[1:-len(":null}")];yield ":"
                        yield from fragments(value[key])
                    yield "}"
                else:
                    yield "["
                    for index,item in enumerate(value):
                        if index:yield ","
                        yield from fragments(item)
                    yield "]"
            finally:active.remove(id(value))
        yield from fragments(payload)
    return produce

def compressed_fragments(stats):
    def produce(encoder,payload):
        active=set();cache={};used=0;capturing=False
        def fragments(value):
            nonlocal used,capturing
            identity=id(value);hit=cache.get(identity)
            if hit is not None:
                assert hit[0] is value
                stats["hits"]+=1;stats["reused_characters"]+=hit[2]
                decoder=zlib.decompressobj()
                for compressed in hit[1]:
                    pending=compressed
                    while pending:
                        data=decoder.decompress(pending,65536);pending=decoder.unconsumed_tail
                        if data:yield data.decode("ascii")
                while True:
                    data=decoder.decompress(b"",65536)
                    if not data:break
                    yield data.decode("ascii")
                assert decoder.eof and not decoder.unused_data,"SNAPSHOT_CACHE_TRUNCATED"
                return
            if identity not in targets or capturing:
                yield from fresh(value);return
            compressor=zlib.compressobj(level=1)
            pieces=[];pending=[];pending_size=0;charge=0;characters=0;fits=True
            def append(piece):
                nonlocal charge,fits
                if not piece:return
                charge+=sys.getsizeof(piece)+16
                if used+charge+512<=4*1024**2:pieces.append(piece)
                else:pieces.clear();fits=False
            capturing=True
            try:
                for fragment in fresh(value):
                    if fits:
                        characters+=len(fragment)
                        if characters>32*1024**2:
                            pieces.clear();pending.clear();fits=False;stats["budget_rejections"]+=1
                        else:
                            if pending and pending_size+len(fragment)>65536:
                                append(compressor.compress("".join(pending).encode("ascii")))
                                pending=[];pending_size=0
                            if fits:pending.append(fragment);pending_size+=len(fragment)
                    yield fragment
                if fits:
                    if pending:append(compressor.compress("".join(pending).encode("ascii")))
                    if fits:append(compressor.flush())
            finally:capturing=False
            if fits:
                cache[identity]=(value,tuple(pieces),characters);used+=charge+512
                stats["entries"]+=1;stats["charged_bytes"]=used
                stats["compressed_cache_bytes"]=sum(len(x) for x in pieces)
        def fresh(value):
            container=isinstance(value,(dict,list,tuple))
            if not container or candidate._small_json_tree(value):
                yield encoder.encode(value);return
            if id(value) in active:raise ValueError("Circular reference detected")
            active.add(id(value))
            try:
                if isinstance(value,dict):
                    yield "{"
                    for index,key in enumerate(sorted(value)):
                        if index:yield ","
                        encoded=encoder.encode({key:None})
                        yield encoded[1:-len(":null}")];yield ":"
                        yield from fragments(value[key])
                    yield "}"
                else:
                    yield "["
                    for index,item in enumerate(value):
                        if index:yield ","
                        yield from fragments(item)
                    yield "]"
            finally:active.remove(id(value))
        yield from fragments(payload)
    return produce

original_small=candidate._small_json_tree
small_source=inspect.getsource(original_small)
needle="len(json.encoder.encode_basestring_ascii(key))"
assert needle in small_source
def key_cached_small():
    lengths={}
    def key_length(key):
        value=lengths.get(key)
        if value is None:
            value=len(json.encoder.encode_basestring_ascii(key))
            if len(key)<=256 and len(lengths)<512:lengths[key]=value
        return value
    env=dict(candidate.__dict__);env["_key_length"]=key_length
    exec(small_source.replace(needle,"_key_length(key)"),env)
    return env["_small_json_tree"]

expected=None;receipts=[]
for variant in ("baseline","raw32","zcache4","zcache_keys4","zcache_keys4","zcache4","raw32","baseline"):
    gc.collect();stats={"hits":0,"reused_characters":0,"entries":0,"charged_bytes":0,"budget_rejections":0,"compressed_cache_bytes":0}
    candidate._small_json_tree=key_cached_small() if variant=="zcache_keys4" else original_small
    candidate._canonical_fragments=(original_fragments if variant=="baseline" else
        cached_fragments(32*1024**2,stats) if variant=="raw32" else compressed_fragments(stats))
    cpu=time.process_time();wall=time.monotonic();raw=hashlib.sha256();size=0
    path=root/(variant+".json.gz")
    with path.open("wb") as output:
        with gzip.GzipFile(filename="",mode="wb",compresslevel=1,fileobj=output,mtime=0) as zipped:
            for block in candidate._canonical_chunks(encoder,report):
                size+=len(block);raw.update(block);zipped.write(block)
    row={"variant":variant,"cpu_seconds":round(time.process_time()-cpu,3),
         "wall_seconds":round(time.monotonic()-wall,3),"raw_bytes":size,
         "raw_sha256":raw.hexdigest(),"compressed_bytes":path.stat().st_size,
         "peak_rss_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,**stats}
    if expected is None:expected=(size,raw.hexdigest())
    assert expected==(size,raw.hexdigest()),"SHARED_SNAPSHOT_JSON_CHANGED"
    assert row["peak_rss_kib"]<=512*1024,"SHARED_SNAPSHOT_RSS_OVER_512MIB"
    receipts.append(row);print("RC6_SHARED_SNAPSHOT_BENCH="+json.dumps(row,sort_keys=True,separators=(",",":")),flush=True)
candidate._canonical_fragments=original_fragments;candidate._small_json_tree=original_small
summary={variant:statistics.median(r["cpu_seconds"] for r in receipts if r["variant"]==variant)
         for variant in {r["variant"] for r in receipts}}
result={"schema":"rc6.shared-frozen-snapshot-benchmark.v1","receipts":receipts,
        "median_cpu_seconds":summary,"canonical_bytes_identical":True,"native_acceptance":False,
        "production_data_mounted":False,"droplet_accessed":False,
        "scope":"Per-pass compressed cache max4MiB, raw_snapshot32MiB, exact shared immutable preopen rows only",
        "provider_requests":0,"real_orders_sent":0}
pathlib.Path("/work/shared-snapshot-benchmark.json").write_text(json.dumps(result,sort_keys=True,indent=2)+"\n")
print("RC6_SHARED_SNAPSHOT_MEDIANS="+json.dumps(summary,sort_keys=True,separators=(",",":")),flush=True)
