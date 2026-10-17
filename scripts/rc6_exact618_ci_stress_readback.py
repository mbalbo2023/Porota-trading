"""Read exact CI stress bytes. This reader cannot certify Droplet acceptance."""
import base64,hashlib,io,json,os,pathlib,urllib.request,zipfile
import xml.etree.ElementTree as ET
ARTIFACT=12097989198
RUN=39026405201
HEAD="f911412ed6e0617ee12bc38893d4f80c1406672a"
SHA="afd5f33d126876704f539d14e03fcb1f33f7359dab624d6c64d61f76c29bdf8e"
def get(url):
    request=urllib.request.Request(url)
    request.add_unredirected_header("Authorization","Bearer "+os.environ["GH_TOKEN"])
    return urllib.request.urlopen(request,timeout=45)
url="https://api.github.com/repos/mbalbo2023/Porota-trading/actions/artifacts/"+str(ARTIFACT)
with get(url) as response:metadata=json.load(response)
assert metadata["workflow_run"]["id"]==RUN and metadata["workflow_run"]["head_sha"]==HEAD
assert metadata["digest"]=="sha256:"+SHA and not metadata["expired"]
with get(url+"/zip") as response:packed=response.read(4*1024**2+1)
assert len(packed)<=4*1024**2 and len(packed)==metadata["size_in_bytes"]
assert hashlib.sha256(packed).hexdigest()==SHA
with zipfile.ZipFile(io.BytesIO(packed)) as archive:
    def read(name):
        matches=[x for x in archive.infolist() if x.filename==name]
        assert len(matches)==1 and matches[0].file_size<=4*1024**2
        raw=archive.read(matches[0]);assert len(raw)==matches[0].file_size
        return raw
    raw=read("stress.json");stress=json.loads(raw)
    assert stress["status"]=="GREEN" and stress["smoke"] is False
    assert stress["evidence_kind"]=="CI_EXACT_IMAGE_PREFLIGHT"
    assert stress["production_data_mounted"] is False and stress["ppi_watch_accessed"] is False
    full=stress["full_catalog"];cuts=full["cuts"]
    assert full["catalog_count"]==14611 and full["ready_fixture_count"]==9902
    assert len(cuts)==2 and cuts[1]["sequence"]>cuts[0]["sequence"]
    assert cuts[1]["previous_cut_age_seconds"]<=cuts[1]["previous_cut_freshness_budget_seconds"]
    assert full["process_peak_rss_kib"]<=512*1024
    session=stress["session"]
    assert session["simulated_minutes"]==405 and session["counts"]["paper_fills"]>0
    assert type(session["real_orders_sent"]) is int and session["real_orders_sent"]==0
    assert session["provider_requests"]==0 and session["real_routes"]=="NOT_CALLED"
    assert session["actual_market_acceptance"] is False
    groups=stress["scenarios"];assert len(groups)==8 and len({x["name"] for x in groups})==8
    xmls=[]
    for group in groups:
        data=read(group["name"]+".xml")
        assert hashlib.sha256(data).hexdigest()==group["junit_sha256"]
        cases=list(ET.fromstring(data).iter("testcase"))
        counts={key:sum(len(case.findall(key)) for case in cases) for key in ("failure","error","skipped")}
        assert len(cases)>0 and len(cases)==group["cases"]
        assert group["exit_code"]==0 and not any(counts.values())
        xmls.append({"name":group["name"],"cases":len(cases),"sha256":group["junit_sha256"],**counts})
    assert sum(x["cases"] for x in xmls)==756
result={"schema":"rc6.exact-image-ci-full-stress-readback.v1","status":"GREEN",
        "source_run":RUN,"source_job":117378170472,"diagnostic_sha":HEAD,
        "candidate_sha":"5cb663231b52aee566dec0b3bf9c5d45684b98f5",
        "candidate_tree":"2f1825afed2417e4d4d7a8f003fecc009708921f",
        "artifact_id":ARTIFACT,"artifact_zip_sha256":SHA,"artifact_bytes":len(packed),
        "stress_json_sha256":hashlib.sha256(raw).hexdigest(),"groups":xmls,
        "total_cases":756,"full_catalog":full,"session":session,
        "elapsed_seconds":stress["elapsed_seconds"],"self_peak_rss_kib":stress["self_peak_rss_kib"],
        "child_peak_rss_kib":stress["child_peak_rss_kib"],"evidence_kind":"CI_EXACT_IMAGE_PREFLIGHT",
        "native_acceptance":False,"full_stress_inside_droplet":False,
        "production_runtime_restore":"NOT_PERFORMED_BY_CI","ppi_watch_accessed":False,
        "production_data_mounted":False,"real_orders_sent":0,"provider_requests":0}
saved=(json.dumps(result,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
pathlib.Path("exact618-ci-full-stress-readback.json").write_bytes(saved)
print("RC6_EXACT618_CI_READBACK_SHA256="+hashlib.sha256(saved).hexdigest(),flush=True)
print("RC6_EXACT618_CI_READBACK_BYTES="+str(len(saved)),flush=True)
encoded=base64.b64encode(saved).decode("ascii")
parts=[encoded[n:n+32768] for n in range(0,len(encoded),32768)]
for n,part in enumerate(parts):print("RC6_EXACT618_CI_READBACK_B64_PART="+str(n)+"/"+str(len(parts))+":"+part,flush=True)
print("RC6_EXACT618_CI_READBACK="+json.dumps(result,sort_keys=True,separators=(",",":")),flush=True)
