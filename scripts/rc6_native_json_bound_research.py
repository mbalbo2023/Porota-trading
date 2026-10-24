#!/usr/bin/env python3
"""Research only: compile-free caller for bounded C sizing vs exact Python semantics."""
import argparse,gc,gzip,hashlib,importlib.util,json,math,os,random,resource,subprocess,sys,sysconfig,time
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,"/app")
from rc6_shadow_runtime import persistence as p
from rc6_shadow_runtime import worker as runtime
from scripts.rc6_postdeploy_stress import FULL_CATALOG_SHAPE,full_catalog_replay
from tests.test_rc6_shadow_runtime_wiring import PRE
from be_paper_engine import PaperStore
from bf_production_paper_observer import _support_schema
from cf_intraday_scalping import init_schema
BASELINE=p._small_json_tree
def load_native(path):
    spec=importlib.util.spec_from_file_location("_rc6_json_bound",path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module
def signature(call):
    try:return ("VALUE",call())
    except Exception as error:return ("ERROR",type(error).__name__)
def selfcheck(native):
    rng=random.Random(603);count=0
    atoms=[None,True,False,0,-1,2**100,1.25,-0.0,"","ASCII","á漢字😀","\\\"\n\t\x00\x7f","\ud800"]
    def tree(depth=0):
        if depth>=4 or rng.randrange(3)==0:return rng.choice(atoms)
        kind=rng.randrange(3);size=rng.randrange(8)
        if kind==0:return [tree(depth+1) for _ in range(size)]
        if kind==1:return tuple(tree(depth+1) for _ in range(size))
        return {str(n)+rng.choice(["","á","\\\""]):tree(depth+1) for n in range(size)}
    fixed=atoms+[{str(n):n for n in range(256)},{str(n):n for n in range(257)},
        list(range(256)),list(range(257)),{"a":"x"*65536},{"\\\"":"😀"*12000},
        {None:"a"},{1:"a"},{1.5:"a"},{True:"a"},float("nan"),float("inf"),1<<100000]
    alias={"a":["b",1]};fixed.append([alias,alias])
    cycle=[];cycle.append(cycle);fixed.append(cycle)
    nested=0
    for _ in range(3000):nested=[nested]
    fixed.append(nested)
    class D(dict):pass
    class L(list):pass
    class S(str):pass
    fixed.extend([D(a=1),L([1]),S("s")])
    frozen=p.FrozenJSONDict({"a":"x","b":[]});fixed.append(frozen)
    for value in fixed+[tree() for _ in range(2000)]:
        for maximum,nodes in ((65536,4096),(64,16),(0,0),(16,4096),(65536,1)):
            expected=signature(lambda:BASELINE(value,maximum,nodes))
            actual=signature(lambda:native.is_small(value,p.FrozenJSONDict,maximum,nodes))
            assert actual==expected,("NATIVE_BOUND_DIFFERENTIAL",type(value).__name__,maximum,nodes,expected[0],actual[0])
            count+=1
    def chunks(value,checker):
        with patch.object(p,"_small_json_tree",checker):
            encoder=json.JSONEncoder(sort_keys=True,separators=(",",":"),default=str,allow_nan=False)
            blocks=list(p._canonical_chunks(encoder,value))
        assert all(len(x)<=65536 for x in blocks)
        return b"".join(blocks)
    fast=lambda value,maximum=65536,nodes=4096:native.is_small(value,p.FrozenJSONDict,maximum,nodes)
    encoded=0
    for value in fixed+[tree() for _ in range(150)]:
        expected=signature(lambda:chunks(value,BASELINE))
        actual=signature(lambda:chunks(value,fast))
        assert actual==expected,("NATIVE_CANONICAL_DIFFERENTIAL",type(value).__name__)
        encoded+=1
    object.__setattr__(frozen,"_canonical_json","WRONG_ENCODING_HINT")
    assert chunks(frozen,fast)==chunks(frozen,BASELINE)==b'{"a":"x","b":[]}'
    object.__delattr__(frozen,"_canonical_json")
    assert signature(lambda:BASELINE(frozen))==signature(lambda:fast(frozen))
    before=sys.getrefcount(alias)
    for _ in range(10000):native.is_small(alias,p.FrozenJSONDict)
    assert sys.getrefcount(alias)==before,"NATIVE_BOUND_REFERENCE_LEAK"
    return {"differential_predicate_cases":count,"canonical_stream_cases":encoded,
        "cycles_aliases_unicode_surrogates_subclasses_integer_float_errors":True,
        "constructor_hint_not_content_authority":True,"reference_count_stable_after10000_calls":True}
def gzip_identity(path):
    h=hashlib.sha256();size=0
    with gzip.open(path,"rb") as source:
        while block:=source.read(65536):h.update(block);size+=len(block)
    return size,h.hexdigest()
def run(output,extension,source):
    output.mkdir(parents=True,exist_ok=True)
    assert extension.is_file(),"PRECOMPILED_RESEARCH_BINARY_REQUIRED"
    native=load_native(extension)
    controls=selfcheck(native);gc.collect()
    print("RC6_NATIVE_BOUND_DIFFERENTIAL="+json.dumps(controls,sort_keys=True),flush=True)
    folder=output/"baseline";folder.mkdir()
    store=PaperStore(str(folder/"paper.db"));_support_schema(store);init_schema(store)
    rows=[]
    for family,(total,ready) in FULL_CATALOG_SHAPE.items():
        for n in range(total):
            rows.append((f"LOAD_{family}_{n:05d}",family,"BYMA",
                ("ARS","USD_MEP","USD_CCL")[n%3],"A-24HS","SYNTHETIC",
                "synthetic public-cardinality fixture",PRE.isoformat(),"stress-full-catalog",
                "AVAILABLE" if n<ready else "PAUSED_EXPLICIT",
                "READY_PAPER_SPOT" if n<ready else "NO_VERIFICADO","{}"))
    with store.connect() as c:c.executemany("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",rows)
    del rows;gc.collect()
    worker=runtime.ShadowRuntime(store.path,evidence_root=folder/"shadow",source_roots=[])
    original_write=p.EvidenceFiles.write;checkpoint=[]
    def observed_write(self,name,payload,**kwargs):
        if name=="checkpoint.json.gz":checkpoint.append(payload)
        return original_write(self,name,payload,**kwargs)
    with patch.object(p.EvidenceFiles,"write",observed_write):report=worker.tick(PRE)
    assert len(checkpoint)==1 and report["status"]=="SHADOW_OBSERVING"
    assert report["provider_requests"]==0 and type(report["real_orders_sent"]) is int and report["real_orders_sent"]==0
    targets=runtime.shared_preopen_row_lists(report)
    fast=lambda value,maximum=65536,nodes=4096:native.is_small(value,p.FrozenJSONDict,maximum,nodes)
    receipts=[];expected={}
    for mode,checker in (("PYTHON_BASELINE",BASELINE),("C_SIZING_ONLY",fast),("C_SIZING_ONLY",fast),("PYTHON_BASELINE",BASELINE)):
        for role in ("FULL_COMPUTED_AUDIT","CAUSAL_CHECKPOINT"):
            gc.collect();wall=time.monotonic();cpu=time.process_time()
            with patch.object(p,"_small_json_tree",checker),worker.files as files:
                if role=="FULL_COMPUTED_AUDIT":
                    result=files.write_streamed_report(report,immutable_shared_rows=targets)
                    identity=(result["raw_bytes"],result["sha256"])
                else:
                    files.write("checkpoint-benchmark.json.gz",checkpoint[0])
                    identity=None
            cpu_elapsed=time.process_time()-cpu;wall_elapsed=time.monotonic()-wall
            if role=="CAUSAL_CHECKPOINT":identity=gzip_identity(worker.root/"checkpoint-benchmark.json.gz")
            if role in expected:assert identity==expected[role],("NATIVE_BOUND_JSON_CHANGED",role)
            else:expected[role]=identity
            receipt={"mode":mode,"role":role,"wall_seconds":round(wall_elapsed,6),
                "cpu_seconds":round(cpu_elapsed,6),"raw_bytes":identity[0],"sha256":identity[1]}
            receipts.append(receipt)
            print("RC6_NATIVE_BOUND_BENCH="+json.dumps(receipt,sort_keys=True,separators=(",",":")),flush=True)
    del report,checkpoint,targets,worker,store;gc.collect()
    with patch.object(p,"_small_json_tree",fast):
        cadence=full_catalog_replay(output/"candidate-cadence")
    assert resource.getrusage(resource.RUSAGE_SELF).ru_maxrss<=512*1024
    result={"schema":"rc6.native-json-bound-research.v1","context":"CI_C_SIZING_RESEARCH_OVER_EXACT618_BASE_NOT_PRODUCT_CANDIDATE",
        "base_candidate_sha":"5cb663231b52aee566dec0b3bf9c5d45684b98f5",
        "base_image_id":"sha256:2f581a5cc45e34dad4974d35398aeef89f3dbc4c9ca7f4c3c172efd1123b62d9",
        "source_sha256":hashlib.sha256(source.read_bytes()).hexdigest(),
        "extension_sha256":hashlib.sha256(extension.read_bytes()).hexdigest(),
        "controls":controls,"receipts":receipts,"full_catalog_candidate":cadence,
        "serialized_bytes_unchanged":True,"small_tree_maximum":65536,"small_tree_nodes":4096,
        "peak_rss_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "provider_requests":0,"real_orders_sent":0,"real_routes":"NOT_CALLED",
        "product_dependencies_changed":False,"product_source_changed":False,
        "production_data_mounted":False,"droplet_accessed":False,"native_acceptance":False,"full_session_stress":False}
    (output/"native-bound-research.json").write_text(json.dumps(result,sort_keys=True,indent=2)+"\n")
    print("RC6_NATIVE_BOUND_RESEARCH="+json.dumps(result,sort_keys=True,separators=(",",":")),flush=True)
if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--extension",type=Path,required=True);parser.add_argument("--source",type=Path,required=True)
    args=parser.parse_args();run(args.output,args.extension,args.source)
