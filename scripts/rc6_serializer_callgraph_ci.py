#!/usr/bin/env python3
"""CI-only exact-image synthetic serializer call graph; no native acceptance."""
import argparse,cProfile,gc,hashlib,json,os,pstats,re,resource,sys,time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,"/app")
from be_paper_engine import PaperStore
from bf_production_paper_observer import _support_schema
from cf_intraday_scalping import init_schema
from tests.test_rc6_shadow_runtime_wiring import PRE
from scripts.rc6_postdeploy_stress import FULL_CATALOG_SHAPE
from rc6_shadow_runtime import worker as runtime
from rc6_shadow_runtime import persistence
from rc6_shadow_runtime.persistence import EvidenceFiles
CANDIDATE="5cb663231b52aee566dec0b3bf9c5d45684b98f5"
TREE="2f1825afed2417e4d4d7a8f003fecc009708921f"
def run(output):
    output.mkdir(parents=True,exist_ok=True)
    folder=output/"synthetic";folder.mkdir()
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
    total=len(rows);del rows;gc.collect()
    worker=runtime.ShadowRuntime(store.path,evidence_root=folder/"shadow",source_roots=[])
    original=EvidenceFiles.write_streamed_report
    inflater=persistence.zlib.decompressobj
    captured=[]
    prof=cProfile.Profile()
    def observed(self,payload,**kwargs):
        targets=kwargs.get("immutable_shared_rows",())
        engines=payload.get("frozen",{})
        lists=[v.get("payload",{}).get("rows") for v in engines.values() if isinstance(v,dict)]
        lists=[x for x in lists if type(x) is list]
        counts={"shared_target_lists":len(targets),"shared_target_rows":[len(x) for x in targets],
            "frozen_engine_lists":len(lists),"unique_engine_list_identities":len({id(x) for x in lists}),
            "cache_replay_inflater_creations":0}
        def factory(*a,**kw):
            counts["cache_replay_inflater_creations"]+=1
            return inflater(*a,**kw)
        started=time.monotonic();cpu=time.process_time()
        with patch.object(persistence.zlib,"decompressobj",factory):
            value=original(self,payload,**kwargs)
        counts.update(wall_seconds=time.monotonic()-started,cpu_seconds=time.process_time()-cpu,audit=value)
        captured.append(counts)
        return value
    with patch.object(EvidenceFiles,"write_streamed_report",observed):
        report=prof.runcall(worker.tick,PRE)
    assert report["status"]=="SHADOW_OBSERVING"
    assert type(report["real_orders_sent"]) is int and report["real_orders_sent"]==0
    assert report["provider_requests"]==0 and report["real_routes"]=="NOT_CALLED"
    assert len(captured)==1
    targets=runtime.shared_preopen_row_lists(report)
    expected=captured[0]["audit"]
    benchmarks=[]
    for mode,reusable in (("NO_MEMO",()),("COMPRESSED_SHARED_MEMO",targets),("NO_MEMO",()),("COMPRESSED_SHARED_MEMO",targets)):
        gc.collect();started=time.monotonic();cpu=time.process_time()
        with worker.files as files:
            value=original(files,report,immutable_shared_rows=reusable)
        assert value["sha256"]==expected["sha256"] and value["raw_bytes"]==expected["raw_bytes"]
        benchmarks.append({"mode":mode,"wall_seconds":round(time.monotonic()-started,6),
            "cpu_seconds":round(time.process_time()-cpu,6),"raw_bytes":value["raw_bytes"],
            "sha256":value["sha256"]})
    groups={};visits=[0]
    def inspect_lists(value,path,active):
        visits[0]+=1
        if visits[0]>200000:raise ValueError("REPORT_IDENTITY_INSPECTION_VISIT_LIMIT")
        if isinstance(value,(dict,list)) and len(value)>256:
            group=groups.setdefault(id(value),{"rows":len(value),"kind":type(value).__name__,"paths":[],"value":value})
            if len(group["paths"])<16:group["paths"].append(path)
            return
        if not isinstance(value,(dict,list,tuple)):return
        if id(value) in active:raise ValueError("REPORT_IDENTITY_INSPECTION_CYCLE")
        active.add(id(value))
        if isinstance(value,dict):
            for key,item in value.items():
                if isinstance(item,(dict,list,tuple)):inspect_lists(item,path+"."+str(key),active)
        else:
            for n,item in enumerate(value):
                if isinstance(item,(dict,list,tuple)):inspect_lists(item,path+"[]",active)
        active.remove(id(value))
    inspect_lists(report,"report",set())
    arrays=[{"rows":v["rows"],"kind":v["kind"],"occurrences":len(v["paths"]),"paths":v["paths"],
        "supplied_shared_target":any(v["value"] is x for x in targets)} for v in groups.values()]
    stats=pstats.Stats(prof).stats
    hot=[]
    for (filename,line,function),(primitive,total_calls,own,cumulative,callers) in stats.items():
        name=Path(filename).name
        if not (filename.startswith("/app/") or name in {"encoder.py","gzip.py","json","copy.py","~","_strptime.py"}):continue
        label=re.sub(r"[^A-Za-z0-9_.<> -]","_",function)[:100]
        hot.append({"module":name[:80],"line":line,"function":label,"primitive_calls":primitive,
            "total_calls":total_calls,"own_profile_wall_seconds":round(own,6),"cumulative_profile_wall_seconds":round(cumulative,6)})
    hot.sort(key=lambda x:x["own_profile_wall_seconds"],reverse=True)
    result={"schema":"rc6.exact618-whole-worker-callgraph.v2","context":"CI_EXACT_IMAGE_SERIALIZER_RCA_NOT_NATIVE",
        "candidate_sha":CANDIDATE,"candidate_tree_sha":TREE,
        "image_id":"sha256:2f581a5cc45e34dad4974d35398aeef89f3dbc4c9ca7f4c3c172efd1123b62d9",
        "catalog_count":total,"ready_fixture_count":sum(x[1] for x in FULL_CATALOG_SHAPE.values()),
        "production_data_mounted":False,"provider_requests":0,"real_orders_sent":0,"real_routes":"NOT_CALLED",
        "native_acceptance":False,"full_session_stress":False,"runtime_restore":"NOT_APPLICABLE_CI_ONLY",
        "profile_instrumentation_changes_elapsed_time":True,"profile_timer":"DEFAULT_REAL_TIME_NOT_PROCESS_CPU",
        "prior_profile39033636402_errata":"pstats *_cpu_seconds field names mean default profiler wall seconds; separate benchmark cpu_seconds uses process_time correctly",
        "profiled_audit":captured[0],
        "large_list_identity_groups":arrays,"identity_scan_visits":visits[0],
        "top_own_profile_wall":hot[:40],"benchmarks_same_exact_full_audit":benchmarks,
        "peak_rss_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    assert result["catalog_count"]==14611 and result["ready_fixture_count"]==9902
    assert result["peak_rss_kib"]<=512*1024
    raw=(json.dumps(result,sort_keys=True,indent=2)+"\n").encode()
    (output/"serializer-profile.json").write_bytes(raw)
    print("RC6_EXACT618_WHOLE_WORKER_PROFILE="+json.dumps(result,sort_keys=True,separators=(",",":")),flush=True)
    print("RC6_EXACT618_WHOLE_WORKER_PROFILE_SHA256="+hashlib.sha256(raw).hexdigest(),flush=True)
if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--output",type=Path,required=True);args=parser.parse_args()
    run(args.output)
