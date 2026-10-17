#!/usr/bin/env python3
"""Exercise governed quiesce and exact restore on synthetic CI containers only."""
import argparse, gzip, hashlib, json, os, pathlib, re, shutil, sqlite3, subprocess, tempfile, time
from datetime import datetime,timezone

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),default=str,allow_nan=False).encode()).hexdigest()

def command(args,timeout=40):
    result=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=timeout)
    if result.returncode:raise RuntimeError("CI_DOCKER_COMMAND_FAILED")
    return result.stdout.strip()

def main():
    parser=argparse.ArgumentParser();parser.add_argument("--image",required=True)
    parser.add_argument("--packet",required=True);parser.add_argument("--driver",required=True)
    args=parser.parse_args()
    assert re.fullmatch(r"sha256:[0-9a-f]{64}",args.image)
    packet=pathlib.Path(args.packet).resolve();packet_sha=hashlib.sha256(packet.read_bytes()).hexdigest()
    driver=pathlib.Path(args.driver).read_text()
    run_id=os.environ["GITHUB_RUN_ID"];assert run_id.isdigit()
    base=pathlib.Path(tempfile.mkdtemp(prefix="rc6-ci-governor-"))
    created=[];incoming=[];cases=[]
    try:
        data=base/"data";paper=data/"paper_v17";shadow=paper/"artifacts/observer_v17.db/dynamic-shadow"
        shadow.mkdir(parents=True)
        for path in (base,*shadow.parents):
            if path==pathlib.Path("/tmp"):break
            os.chmod(path,0o777)
        os.chmod(shadow,0o777)
        db=paper/"observer_v17.db"
        stamp=datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(db) as connection:
            connection.execute("CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER,process_state TEXT,session_state TEXT,heartbeat_at TEXT)")
            connection.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0,'WAITING_MARKET','MARKET_CLOSED',?)",(stamp,))
            for table in ("paper_positions","paper_cauciones","paper_future_positions"):
                connection.execute("CREATE TABLE "+table+"(status TEXT)")
        os.chmod(db,0o666)
        payload=bytes(range(256))*16384;raw_sha=hashlib.sha256(payload).hexdigest()
        filename="computed-"+raw_sha+".json.gz"
        with gzip.open(shadow/filename,"wb",compresslevel=1) as output:output.write(payload)
        report={"schema":"CI_SYNTHETIC_REPORT","as_of":stamp,"real_orders_sent":0,"real_routes":"NOT_CALLED",
                "computed_report":{"file":filename,"sha256":raw_sha,"raw_bytes":len(payload),"compressed_bytes":(shadow/filename).stat().st_size}}
        status={"schema":"CI_SYNTHETIC_STATUS","as_of":stamp,"real_orders_sent":0,"real_routes":"NOT_CALLED",
                "provider_requests":0,"mode":"SHADOW","status":"SHADOW_OBSERVING","generation_timing":{"tick_seconds":0}}
        generation=digest({"report":report,"status":status});configuration="1"*64;watermark={"as_of":stamp}
        members={role:{**value,"generation_id":generation,"source_watermark":watermark,
                      "configuration_fingerprint":configuration} for role,value in (("report",report),("status",status))}
        manifest={"schema":"rc6.dashboard-generation.v1","generation_id":generation,"sequence":1,
                  "source_watermark":watermark,"configuration_fingerprint":configuration,
                  "files":{role:{"payload_digest":digest(value)} for role,value in members.items()}}
        def envelope(path,value):
            raw=json.dumps({"payload":value,"digest":digest(value)},sort_keys=True,separators=(",",":")).encode()
            if path.name.endswith(".gz"):
                with gzip.open(path,"wb",compresslevel=1) as output:output.write(raw)
            else:path.write_bytes(raw)
            os.chmod(path,0o666)
        cut_name="cut-"+generation+".json.gz"
        envelope(shadow/cut_name,{"manifest":manifest,**members})
        envelope(shadow/"CURRENT.json",{"file":cut_name,"generation_id":generation,"sequence":1,"manifest_sha256":digest(manifest)})
        envelope(shadow/"status.json",status)
        envelope(shadow/"latest.json.gz",report);envelope(shadow/"checkpoint.json.gz",{"as_of":stamp,"private":"SYNTHETIC"})
        observer=r'''import hashlib,json,pathlib,signal,sqlite3,sys,time
from datetime import datetime,timezone
signal.signal(signal.SIGTERM,lambda *_:sys.exit(0));signal.signal(signal.SIGINT,lambda *_:sys.exit(0))
root=pathlib.Path("/app/data/paper_v17")
while True:
 stamp=datetime.now(timezone.utc).isoformat()
 with sqlite3.connect(root/"observer_v17.db",timeout=1) as c:c.execute("UPDATE observer_state SET heartbeat_at=? WHERE id=1",(stamp,))
 status={"as_of":stamp,"status":"SHADOW_OBSERVING","generation_timing":{"tick_seconds":0},"mode":"SHADOW","real_orders_sent":0,"provider_requests":0,"real_routes":"NOT_CALLED"}
 encoded=json.dumps(status,sort_keys=True,separators=(",",":")).encode()
 raw=json.dumps({"payload":status,"digest":hashlib.sha256(encoded).hexdigest()},sort_keys=True,separators=(",",":"))
 directory=root/"artifacts/observer_v17.db/dynamic-shadow"
 temporary=directory/"status-ci.tmp";temporary.write_text(raw);temporary.replace(directory/"status.json")
 time.sleep(.5)
'''
        dashboard=r'''import http.server,signal,sys
signal.signal(signal.SIGTERM,lambda *_:sys.exit(0));signal.signal(signal.SIGINT,lambda *_:sys.exit(0))
class Handler(http.server.BaseHTTPRequestHandler):
 def do_GET(self):self.send_response(200);self.end_headers();self.wfile.write(b"CI_SYNTHETIC")
 def log_message(self,*args):pass
http.server.HTTPServer(("127.0.0.1",8000),Handler).serve_forever()
'''
        for name,source in (("porota_production_observer",observer),("porota_production_dashboard",dashboard)):
            existing=subprocess.run(["docker","inspect",name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            assert existing.returncode!=0,"CI_FIXTURE_NAME_ALREADY_OWNED"
            identity=command(["docker","create","--name",name,"--network","host","--user","1000:1000",
                "--read-only","--cap-drop","ALL","--security-opt","no-new-privileges",
                "--tmpfs","/tmp:rw,nosuid,nodev,size=32m","--mount","type=bind,src="+str(data)+",dst=/app/data",
                "--entrypoint","python",args.image,"-c",source])
            assert re.fullmatch("[0-9a-f]{64}",identity);created.append(identity)
            command(["docker","start",identity])
        time.sleep(2)
        root_old='ROOT=Path("/opt/porota-trading").resolve(strict=True)'
        image_old='IMAGE="sha256:bc406686ad8774469e72def0ad94d468e2320de56d790c830f90f20d2a7b6fed"'
        assert driver.count(root_old)==1 and driver.count(image_old)==1
        fixture_driver=driver.replace(root_old,"ROOT=Path("+repr(str(base))+").resolve(strict=True)").replace(image_old,"IMAGE="+repr(args.image))
        for index,injected in enumerate((False,True)):
            key=run_id+"-"+str(index);target=pathlib.Path("/tmp")/("rc6-native-compress-"+key+".tgz")
            assert not target.exists();shutil.copyfile(packet,target);incoming.append(target)
            source=fixture_driver
            if injected:
                anchor='        report["phase"]="PROBE"'
                assert source.count(anchor)==1
                source=source.replace(anchor,'        raise ValueError("RCA_SYNTHETIC_FAILURE_AFTER_QUIESCE")\n'+anchor)
            path=base/("driver-"+str(index)+".py");path.write_text(source)
            run=subprocess.run(["python3",str(path),"--key",key,"--packet",str(target),"--packet-sha",packet_sha],
                               stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=240)
            row=json.loads((data/"deploy/diagnostics/compression"/key/"host-summary.json").read_text())
            assert row["runtime_restore"]=="GREEN" and row["exact_original_runtime_recovered"] is True
            assert row["real_orders_sent"]==0 and set(row["stop_evidence"])=={"porota_production_observer","porota_production_dashboard"}
            assert all(x["classification"]=="CLEAN_EXIT" for x in row["stop_evidence"].values())
            assert all(x["identity_sha256_before"]==x["identity_sha256_after"]
                and x["image_id_before"]==x["image_id_after"]==args.image
                and x["restart_count_before"]==x["restart_count_after"]==0
                and x["status_after"]=="running" for x in row["runtime_lifecycle_checks"].values())
            assert datetime.fromisoformat(row["heartbeat_after"])>datetime.fromisoformat(row["heartbeat_before"])
            if injected:
                assert run.returncode==2 and row["status"]=="RED" and row["error_code"]=="RCA_SYNTHETIC_FAILURE_AFTER_QUIESCE"
            else:
                assert run.returncode==0 and row["status"]=="GREEN" and len(row["metrics"]["measurements"])==6
            cases.append({"case":"INJECTED_FAILURE" if injected else "SUCCESS","status":"GUARD_VERIFIED",
                          "runtime_restore":row["runtime_restore"],"source_receipt":row})
        receipt={"schema":"rc6.quiesce-governor-selfcheck.v1","execution_context":"CI_DOCKER_FIXTURE",
                 "actual_droplet_accessed":False,"native_acceptance":False,"status":"GREEN","cases":cases}
        pathlib.Path("quiesce-governor-selfcheck.json").write_text(json.dumps(receipt,sort_keys=True,indent=2)+"\n")
        print("RC6_QUIESCE_GOVERNOR_SELFCHECK=GREEN|success_and_injected_failure|same_ids_images_restarts|heartbeat_advanced",flush=True)
    finally:
        for identity in created:
            subprocess.run(["docker","rm","--force",identity],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20)
        for path in incoming:path.unlink(missing_ok=True)
        shutil.rmtree(base,ignore_errors=True)

if __name__=="__main__":main()
