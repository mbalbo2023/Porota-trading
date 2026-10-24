"""Verify numeric-only shared audit qualification from exact CI artifact bytes."""
import base64,hashlib,io,json,os,pathlib,urllib.request,zipfile
import xml.etree.ElementTree as ET
REPO="mbalbo2023/Porota-trading"
ARTIFACT=12098526003
RUN=39025376522
HEAD="dbfec74040aa133ac199b78f4cd98ac3e103aafb"
CANDIDATE="5cb663231b52aee566dec0b3bf9c5d45684b98f5"
ZIP_SHA="e46e09e80f4213bd7b898e439cca679e9128d0a3f06069fe34cb98ffd89867c6"
def request(url):
    req=urllib.request.Request(url)
    req.add_unredirected_header("Authorization","Bearer "+os.environ["GH_TOKEN"])
    return urllib.request.urlopen(req,timeout=45)
with request("https://api.github.com/repos/"+REPO+"/actions/artifacts/"+str(ARTIFACT)) as response:
    metadata=json.load(response)
assert metadata["workflow_run"]["id"]==RUN and metadata["workflow_run"]["head_sha"]==HEAD
assert metadata["digest"]=="sha256:"+ZIP_SHA and not metadata["expired"]
with request("https://api.github.com/repos/"+REPO+"/actions/artifacts/"+str(ARTIFACT)+"/zip") as response:
    packed=response.read(20*1024**2+1)
assert len(packed)==metadata["size_in_bytes"] and len(packed)<=20*1024**2
assert hashlib.sha256(packed).hexdigest()==ZIP_SHA
with zipfile.ZipFile(io.BytesIO(packed)) as archive:
    def read(name,maximum=4*1024**2):
        members=[x for x in archive.infolist() if x.filename==name]
        assert len(members)==1 and members[0].file_size<=maximum
        raw=archive.read(members[0])
        assert len(raw)==members[0].file_size
        return raw
    names=["budget-tests.xml","candidate-tests.xml"]+["single-flight-repeat-"+str(i)+".xml" for i in range(10)]
    xmls=[]
    for name in names:
        raw=read(name);root=ET.fromstring(raw);cases=list(root.iter("testcase"))
        counts={key:sum(len(case.findall(key)) for case in cases) for key in ("failure","error","skipped")}
        assert cases and not any(counts.values()),(name,counts)
        if name=="candidate-tests.xml":assert len(cases)==58
        if name.startswith("single-flight-repeat-"):assert len(cases)==1
        xmls.append({"file":name,"sha256":hashlib.sha256(raw).hexdigest(),"bytes":len(raw),"cases":len(cases),**counts})
    full_raw=read("full-catalog/full-catalog.json")
    full=json.loads(full_raw)
    assert full["catalog_count"]==14611 and full["ready_fixture_count"]==9902
    assert type(full["real_orders_sent"]) is int and full["real_orders_sent"]==0 and full["provider_requests"]==0
    assert full["process_peak_rss_kib"]<=512*1024
    cuts=full["cuts"];assert len(cuts)==2 and cuts[1]["sequence"]>cuts[0]["sequence"]
    assert cuts[1]["previous_cut_age_seconds"]<=cuts[1]["previous_cut_freshness_budget_seconds"]
    bench_raw=read("shared-snapshot-benchmark.json")
    bench=json.loads(bench_raw)
    assert bench["candidate_sha"]==CANDIDATE and bench["canonical_bytes_identical"] is True
    rows=bench["receipts"];assert len(rows)==4
    assert len({(row["raw_bytes"],row["raw_sha256"]) for row in rows})==1
    assert all(row["cpu_seconds"]>0 and row["peak_rss_kib"]<=512*1024 for row in rows)
    assert bench["cpu_medians"]["compressed_owned"]<bench["cpu_medians"]["baseline"]
    causal_raw=read("single-flight-causal.json")
    causal=json.loads(causal_raw)
    assert causal["status"]=="EXPECTED_FAIL_CLOSED" and causal["fetch_count"]==1
    assert causal["owner"]["result"]=="PPI_BUDGET_STATE_UNAVAILABLE"
    assert causal["owner"]["cause_class"]=="OperationalError" and causal["owner"]["sqlite_errorcode"]==5
    assert causal["follower"]["result"]=="PPI_BOOK_EXIT_DEADLINE_EXCEEDED"
    assert causal["production_budget_code_changed"] is False and causal["droplet_accessed"] is False
result={"schema":"rc6.shared-audit-ci-readback.v1","status":"GREEN","source_run":RUN,
        "diagnostic_sha":HEAD,"candidate_sha":CANDIDATE,"artifact_id":ARTIFACT,
        "artifact_zip_sha256":ZIP_SHA,"artifact_bytes":len(packed),"junit":xmls,
        "full_catalog":full,"benchmark":bench,"causal_control":causal,
        "member_sha256":{"full_catalog":hashlib.sha256(full_raw).hexdigest(),
            "benchmark":hashlib.sha256(bench_raw).hexdigest(),"causal":hashlib.sha256(causal_raw).hexdigest()},
        "causal_qualification":"CONTROLLED_INTERLEAVING_MATCHES_ERRORS; ORIGINAL_FAILED_RUN_NOT_INSTRUMENTED",
        "production_data_mounted":False,"droplet_accessed":False,
        "native_acceptance":False,"full_session_stress":False,"real_orders_sent":0}
saved=(json.dumps(result,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
pathlib.Path("shared-audit-development-readback.json").write_bytes(saved)
print("RC6_SHARED_AUDIT_READBACK_SHA256="+hashlib.sha256(saved).hexdigest(),flush=True)
print("RC6_SHARED_AUDIT_READBACK_BYTES="+str(len(saved)),flush=True)
encoded=base64.b64encode(saved).decode("ascii")
print("RC6_SHARED_AUDIT_READBACK_B64="+encoded,flush=True)
print("RC6_SHARED_AUDIT_READBACK="+json.dumps(result,sort_keys=True,separators=(",",":")),flush=True)
