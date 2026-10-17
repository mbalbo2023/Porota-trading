"""Build a public pinned RCA packet; never download production evidence."""
import base64,gzip,hashlib,io,json,pathlib,shutil,tarfile,urllib.parse,urllib.request,zipfile
VERSION="3.4.0.post0"
NAME="ijson-3.4.0.post0-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl"
SHA="deda4cfcaafa72ca3fa845350045b1d0fef9364ec9f413241bb46988afbe6ee6"
with urllib.request.urlopen("https://pypi.org/pypi/ijson/"+VERSION+"/json",timeout=30) as response:metadata=json.load(response)
choices=[x for x in metadata["urls"] if x["filename"]==NAME]
assert len(choices)==1 and choices[0]["digests"]["sha256"]==SHA
url=choices[0]["url"];parsed=urllib.parse.urlsplit(url)
assert parsed.scheme=="https" and parsed.hostname=="files.pythonhosted.org"
with urllib.request.urlopen(url,timeout=30) as response:raw=response.read(2*1024**2+1)
assert len(raw)<=2*1024**2 and hashlib.sha256(raw).hexdigest()==SHA
root=pathlib.Path("/tmp/rc6-native-packet-reuse");root.mkdir()
module=root/"ijson-module";module.mkdir()
with zipfile.ZipFile(io.BytesIO(raw)) as archive:
    members=archive.infolist()
    assert len(members)<=250 and sum(x.file_size for x in members)<=8*1024**2
    for item in members:
        path=pathlib.PurePosixPath(item.filename)
        assert not path.is_absolute() and ".." not in path.parts
        assert path.parts[0] in {"ijson","ijson.libs"} or path.parts[0].startswith("ijson-3.4.0.post0.dist-info")
        target=module/pathlib.Path(*path.parts)
        if item.is_dir():target.mkdir(parents=True,exist_ok=True);continue
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(archive.read(item))
probe=pathlib.Path("scripts/rc6_audit_rowshape_native_probe.py")
packet=root/"rc6-native-compression-packet.tgz"
with tarfile.open(packet,"w:gz") as archive:
    archive.add(probe,arcname="probe.py")
    archive.add(module,arcname="ijson-module")
digest=hashlib.sha256(packet.read_bytes()).hexdigest()
(root/"rc6-native-compression-packet.sha256").write_text(digest+"\n")
gate=pathlib.Path("docs/releases/evidence603/native618-gate-39026330767.json").read_bytes()
value=json.loads(gate);assert value["source_run"]==39026330767 and value["stress_droplet_executed"] is False
shutil.copyfile("docs/releases/evidence603/native618-gate-39026330767.json",root/"native618-gate-receipt.json")
print("RC6_ROW_SHAPE_PACKET=GREEN|sha256="+digest+"|wheel_sha256="+SHA,flush=True)
print("RC6_618_NATIVE_GATE_RECEIPT_SHA256="+hashlib.sha256(gate).hexdigest(),flush=True)
print("RC6_618_NATIVE_GATE_RECEIPT_BYTES="+str(len(gate)),flush=True)
print("RC6_618_NATIVE_GATE_RECEIPT_B64="+base64.b64encode(gate).decode("ascii"),flush=True)
