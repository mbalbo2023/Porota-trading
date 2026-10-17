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

import gzip
from isal import igzip
baseline._small_json_tree=original_small
exec(original_fragments,baseline.__dict__)
receipts=[];expected=None
def read_hash(path,opener):
    h=hashlib.sha256();size=0;cpu=time.process_time();wall=time.monotonic()
    with opener(path,"rb") as stream:
        while block:=stream.read(65536):size+=len(block);h.update(block)
    return size,h.hexdigest(),round(time.process_time()-cpu,3),round(time.monotonic()-wall,3)
for variant in ("stdlib","isal0","isal1","isal1","isal0","stdlib"):
    gc.collect();cpu=time.process_time();wall=time.monotonic();h=hashlib.sha256();size=0
    path=root/(variant+".json.gz")
    provider=gzip if variant=="stdlib" else igzip
    level=1 if variant=="stdlib" else int(variant[-1])
    encoder=json.JSONEncoder(sort_keys=True,separators=(",",":"),default=str,allow_nan=False)
    with path.open("wb") as file:
        with provider.GzipFile(filename="",mode="wb",compresslevel=level,fileobj=file,mtime=0) as zipped:
            for block in baseline._canonical_chunks(encoder,report):
                size+=len(block);h.update(block);zipped.write(block)
    write_cpu=time.process_time()-cpu;write_wall=time.monotonic()-wall
    if expected is None:expected=(size,h.hexdigest())
    assert expected==(size,h.hexdigest()),"CANONICAL_JSON_CHANGED"
    standard=read_hash(path,gzip.open);accelerated=read_hash(path,igzip.open)
    assert standard[:2]==accelerated[:2]==expected,"GZIP_READ_COMPATIBILITY_FAILED"
    row={"variant":variant,"write_cpu_seconds":round(write_cpu,3),"write_wall_seconds":round(write_wall,3),
         "raw_bytes":size,"raw_sha256":h.hexdigest(),"compressed_bytes":path.stat().st_size,
         "standard_read_cpu_seconds":standard[2],"accelerated_read_cpu_seconds":accelerated[2],
         "peak_rss_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    assert row["peak_rss_kib"]<=512*1024 and row["compressed_bytes"]<128*1024**2
    receipts.append(row)
    print("RC6_GZIP_COMPAT_BENCH="+json.dumps(row,separators=(",",":")),flush=True)
legacy=list((root/"shadow").glob("computed-*.json.gz"))
assert len(legacy)==1
old_standard=read_hash(legacy[0],gzip.open);new_reader=read_hash(legacy[0],igzip.open)
assert old_standard[:2]==new_reader[:2]
summary={v:statistics.median(r["write_cpu_seconds"] for r in receipts if r["variant"]==v)
         for v in {r["variant"] for r in receipts}}
result={"schema":"rc6.synthetic-gzip-compat-benchmark.v1","isal_version":"1.8.0","receipts":receipts,
        "median_write_cpu_seconds":summary,"raw_bytes_hash_and_stdlib_reader_equal":True,
        "legacy_stdlib_file_read_verified":True,"native_acceptance":False,
        "production_data_mounted":False,"provider_requests":0,"real_orders_sent":0}
pathlib.Path("/work/gzip-compat-benchmark.json").write_text(json.dumps(result,indent=2)+"\n")
print("RC6_GZIP_CPU_MEDIANS="+json.dumps(summary,sort_keys=True,separators=(",",":")))
