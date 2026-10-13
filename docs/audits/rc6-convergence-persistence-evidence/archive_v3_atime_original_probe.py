import json,os,sys,tempfile
from pathlib import Path
import pytest
from tests.test_rc6_component_archive import native,recipe,bytes_at,policy,snapshot

def custody(root):
 i=root.lstat()
 return ((i.st_dev,i.st_ino,i.st_uid,i.st_gid,i.st_mode,i.st_nlink,i.st_size,i.st_blocks,i.st_atime_ns,i.st_mtime_ns,i.st_ctime_ns),snapshot(root))

root,archive,_,directories,receipts=native(Path(tempfile.mkdtemp(prefix="rc6-atime-exact7e-")))
v=recipe(archive,receipts[-1]); packed=archive/v["packs"][0]
raw=bytes_at(packed);packed.write_bytes(raw[:-1]+bytes([raw[-1]^1]))
(root/("archive-ack-"+receipts[-1]["generation_id"]+".json")).unlink()
paths=(root,root.with_name(root.name+".authority"),archive)
before={str(p):custody(p) for p in paths};reader=policy(root,archive)
with pytest.raises((ValueError,OSError)):reader.restore_generation(receipts[-1]["generation_id"])
after_restore={str(p):custody(p) for p in paths}
with pytest.raises((ValueError,OSError)):reader.archive_generation(directories[-1])
after_archive={str(p):custody(p) for p in paths}
result={"schema":"rc6.archive-atime-native-red.v1","source_sha":"7e9425e202b9e67e87aad67dd59218c509a01721","scope":"PRIVATE_OFFLINE_ORIGINAL_PRODUCT_WITH_EXTERNAL_PROBE","restore_unchanged":before==after_restore,"archive_unchanged":before==after_archive,"archive_root_before":before[str(archive)][0],"archive_root_after_restore":after_restore[str(archive)][0],"archive_root_after_archive":after_archive[str(archive)][0],"source_unchanged":all(before[str(p)]==after_archive[str(p)] for p in paths[:2]),"provider_requests":0}
Path("/tmp/rc6-component-atime-7e9425e2-red.json").write_text(json.dumps(result,sort_keys=True,indent=2)+"\n")
print(json.dumps(result,sort_keys=True))
if result["archive_unchanged"]:raise AssertionError("ORIGINAL_PRODUCT_FAILURE_NOT_REPRODUCED")
