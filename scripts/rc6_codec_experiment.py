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

baseline._small_json_tree=original_small
exec(original_fragments,baseline.__dict__)
encoder=json.JSONEncoder(sort_keys=True,separators=(",",":"),default=str,allow_nan=False)
receipts=[]
def measure(value):
    started=time.monotonic();cpu=time.process_time();size=0
    for block in baseline._canonical_chunks(encoder,value):size+=len(block)
    return size,round(time.monotonic()-started,3),round(time.process_time()-cpu,3)
pending=[("root",report,0)]
while pending:
    path,value,depth=pending.pop(0)
    if not isinstance(value,dict):continue
    for key,child in value.items():
        if not isinstance(key,str):continue
        size,wall,cpu=measure(child)
        receipt={"path":path+"."+key,"raw_bytes":size,"wall_seconds":wall,"cpu_seconds":cpu,
                 "type":type(child).__name__}
        if isinstance(child,(dict,list,tuple)):receipt["count"]=len(child)
        if isinstance(child,list) and child:
            first=child[0]
            if isinstance(first,dict):
                receipt["first_record_keys"]=list(first)[:40]
                sample=measure(first);receipt["first_record_raw_bytes"]=sample[0]
                receipt["first_record_children"]={k:{"type":type(v).__name__,"count":len(v)}
                    for k,v in first.items() if isinstance(v,(dict,list,tuple))}
        receipts.append(receipt)
        print("RC6_CODEC_SECTION="+json.dumps(receipt,separators=(",",":")),flush=True)
        if isinstance(child,dict) and size>5*1024**2 and depth<3:
            pending.append((path+"."+key,child,depth+1))
result={"schema":"rc6.synthetic-codec-section-cost.v1","receipts":receipts,"native_acceptance":False,
        "source_sha":"db06f0a20787ffc4d9f1211b7f2f5fdf04557c14",
        "peak_rss_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
assert result["peak_rss_kib"]<=512*1024
pathlib.Path("/work/codec-sections.json").write_text(json.dumps(result,indent=2)+"\n")
