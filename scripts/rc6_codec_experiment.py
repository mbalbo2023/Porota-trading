"""Compare encoders on ONE synthetic full-cardinality report; no native acceptance."""
import gc, hashlib, importlib.util, inspect, json, os, pathlib, resource, statistics, time, zlib
from scripts.rc6_postdeploy_stress import FULL_CATALOG_SHAPE
from be_paper_engine import PaperStore
from bf_production_paper_observer import _support_schema
from cf_intraday_scalping import init_schema
from tests.test_rc6_shadow_runtime_wiring import PRE
from rc6_shadow_runtime import worker as runtime
from rc6_shadow_runtime import persistence as candidate

root=pathlib.Path("/work/bounds");root.mkdir(parents=True,exist_ok=True)
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

import gzip
original_small=candidate._small_json_tree
encoder=json.JSONEncoder(sort_keys=True,separators=(",",":"),default=str,allow_nan=False)
results=[];expected=None
variants={"64k":(65536,4096),"256k":(262144,16384),"1m":(1048576,65536)}
def benchmark(representation,variant):
    global expected
    maximum,nodes=variants[variant]
    calls={"accepted":0,"rejected":0}
    def bounded(value):
        ok=original_small(value,maximum=maximum,nodes=nodes)
        calls["accepted" if ok else "rejected"]+=1
        return ok
    candidate._small_json_tree=bounded
    gc.collect();cpu=time.process_time();wall=time.monotonic();size=0;h=hashlib.sha256()
    path=root/(representation+"-"+variant+".json.gz")
    with path.open("wb") as output:
        with gzip.GzipFile(filename="",mode="wb",compresslevel=1,fileobj=output,mtime=0) as sink:
            for block in candidate._canonical_chunks(encoder,report):
                size+=len(block);h.update(block);sink.write(block)
    row={"representation":representation,"variant":variant,
         "cpu_seconds":round(time.process_time()-cpu,3),
         "wall_seconds":round(time.monotonic()-wall,3),
         "raw_bytes":size,"raw_sha256":h.hexdigest(),"compressed_bytes":path.stat().st_size,
         "peak_rss_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,**calls}
    if expected is None:expected=(size,h.hexdigest())
    assert expected==(size,h.hexdigest()),"CANONICAL_JSON_CHANGED"
    assert row["peak_rss_kib"]<=512*1024,"PROCESS_RSS_OVER_512MIB"
    results.append(row)
    print("RC6_BOUNDED_SUBTREE_BENCH="+json.dumps(row,sort_keys=True,separators=(",",":")),flush=True)
def ordinary_json_containers(value):
    seen=set();memo={};changed={"frozen_dicts":0,"frozen_empty_lists":0,"tuples":0}
    def visit(item):
        kind=type(item);identity=id(item)
        if identity in memo:return memo[identity][1]
        if kind is candidate.FrozenJSONDict:
            output={};memo[identity]=(item,output);changed["frozen_dicts"]+=1
            output.update((key,visit(value)) for key,value in item.items())
            return output
        if kind is candidate._FrozenEmptyList:
            output=[];memo[identity]=(item,output);changed["frozen_empty_lists"]+=1
            return output
        if kind is tuple:
            output=tuple(visit(x) for x in item);memo[identity]=(item,output);changed["tuples"]+=1
            return output
        if kind is dict:
            if identity in seen:return item
            seen.add(identity)
            for key,value in item.items():item[key]=visit(value)
        elif kind is list:
            if identity in seen:return item
            seen.add(identity)
            for index,value in enumerate(item):item[index]=visit(value)
        return item
    output=visit(value)
    print("RC6_ORDINARY_CONTAINER_SHAPE="+json.dumps(changed,sort_keys=True),flush=True)
    return output
for representation in ("immutable_missing_summaries","ordinary_json_containers"):
    if representation=="ordinary_json_containers":
        report=ordinary_json_containers(report);gc.collect()
    for variant in ("64k","256k","1m","1m","256k","64k"):benchmark(representation,variant)
candidate._small_json_tree=original_small
summary={representation:{variant:statistics.median(
    row["cpu_seconds"] for row in results
    if row["representation"]==representation and row["variant"]==variant)
    for variant in variants} for representation in {r["representation"] for r in results}}
receipt={"schema":"rc6.synthetic-bounded-subtree-experiment.v1",
         "candidate_sha":"7bad982559f56323c00f555f0d8bb480dd00dfd0",
         "measurements":results,"median_cpu_seconds":summary,
         "same_canonical_json_across_all_representations":True,"native_acceptance":False,
         "production_data_mounted":False,"provider_requests":0,"real_orders_sent":0,
         "resources":{"memory_mib":512,"cpu":0.75},
         "purpose":"RCA for native durable19.640CPU-s; ordinary representation is a diagnostic proxy, not copied production data"}
pathlib.Path("/work/bounded-subtree-experiment.json").write_text(json.dumps(receipt,indent=2,sort_keys=True)+"\n")
print("RC6_BOUNDED_SUBTREE_MEDIANS="+json.dumps(summary,sort_keys=True,separators=(",",":")),flush=True)
