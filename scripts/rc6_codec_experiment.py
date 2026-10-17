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

import gzip,statistics
targets=runtime.shared_preopen_row_lists(report)
assert len(targets)==1 and len(targets[0])==9902
encoder=json.JSONEncoder(sort_keys=True,separators=(",",":"),default=str,allow_nan=False)
receipts=[]
for round_id in range(2):
    for mode in ("baseline","compressed_owned"):
        path=root/(mode+"-"+str(round_id)+".gz")
        h=hashlib.sha256();raw_bytes=0
        gc.collect();cpu=time.process_time();wall=time.monotonic()
        kwargs={"immutable_shared_rows":targets} if mode=="compressed_owned" else {}
        with path.open("wb") as stream:
            with gzip.GzipFile(fileobj=stream,mode="wb",compresslevel=1,mtime=0,filename="") as zipped:
                for block in candidate._canonical_chunks(encoder,report,**kwargs):
                    h.update(block);raw_bytes+=len(block);zipped.write(block)
        receipt={"mode":mode,"round":round_id,"raw_bytes":raw_bytes,"raw_sha256":h.hexdigest(),
                 "cpu_seconds":round(time.process_time()-cpu,4),
                 "wall_seconds":round(time.monotonic()-wall,4),
                 "peak_rss_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
        readback=hashlib.sha256();read_bytes=0
        with gzip.open(path,"rb") as stream:
            while block:=stream.read(65536):
                readback.update(block);read_bytes+=len(block)
        assert readback.hexdigest()==receipt["raw_sha256"] and read_bytes==raw_bytes
        assert 100*1024**2<raw_bytes<512*1024**2
        assert receipt["peak_rss_kib"]<=512*1024
        receipts.append(receipt)
        print("RC6_SHARED_AUDIT_CANDIDATE_MEASURE="+json.dumps(receipt,sort_keys=True,separators=(",",":")),flush=True)
assert len({(x["raw_bytes"],x["raw_sha256"]) for x in receipts})==1
medians={mode:statistics.median(x["cpu_seconds"] for x in receipts if x["mode"]==mode)
         for mode in ("baseline","compressed_owned")}
assert medians["compressed_owned"]<medians["baseline"],medians
result={"candidate_sha":"2c7f5bc22057bb5ffbd37ce636d0da3fe30da5af","canonical_bytes_identical":True,
        "cpu_medians":medians,"receipts":receipts,"native_acceptance":False,"full_session_stress":False}
pathlib.Path("/work/shared-snapshot-benchmark.json").write_text(json.dumps(result,sort_keys=True,indent=2)+"\n")
print("RC6_SHARED_AUDIT_CANDIDATE_MEDIANS="+json.dumps(medians,sort_keys=True),flush=True)
