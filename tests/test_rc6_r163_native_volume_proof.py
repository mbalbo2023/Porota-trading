"""R163 HITO D: pure OFFHOST Docker-inspect and collector-TAR contracts.

Fixtures are in memory; these tests never run Docker, sudo, SSH, or Actions.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest


SHA = "d" * 40
TREE = "f" * 40
IMAGE = "sha256:" + "c" * 64
SOURCE_CID = "a" * 64
COLLECTOR_CID = "b" * 64
VOLUME = "e" * 64
RUN_ID = "43086716847"
RUN_ATTEMPT = "1"
ROLE = "bundle"
FILES = (
    "native-bound.xml",
    "session/dashboard_auth_52.xml",
    "session/publication_io_identity.xml",
    "session/scalping_capacity.xml",
    "session/scheduler_monitor_backup.xml",
    "session/signals_risk_exit_ledger.xml",
    "session/sources_readiness_network.xml",
    "session/sql_shadow_wal.xml",
    "session/treasury_caucion.xml",
    "session/stress.json",
    "catalog/full-catalog.json",
    "cadence/cadence-fresh.json",
    "cadence/cadence-legacy.json",
)


def _sut():
    return importlib.import_module("scripts.rc6_r163_native_volume_proof")


def _inspect(*, collector: bool = False) -> dict:
    cid = COLLECTOR_CID if collector else SOURCE_CID
    labels = {
        "porota.commit": SHA,
        "porota.candidate_tree": TREE,
        "porota.predeploy": "v2",
        "porota.native-proof": "v3",
        "porota.native-role": ROLE + ("-collector" if collector else ""),
        "porota.run_id": RUN_ID,
        "porota.run_attempt": RUN_ATTEMPT,
    }
    return {
        "Id": cid,
        "Image": IMAGE,
        "Name": "/porota-r163-" + ("collector" if collector else "producer"),
        "Config": {"User": "1000:1000", "Labels": labels},
        "HostConfig": {
            "NetworkMode": "none", "ReadonlyRootfs": True,
            "CapDrop": ["ALL"], "Privileged": False,
            "SecurityOpt": ["no-new-privileges"],
            # Synthetic neutral Docker Engine 24 Linux representations;
            # runner inspect defaults are not physically verified here.
            "IpcMode": "private", "UTSMode": "", "PidMode": "",
            "UsernsMode": "", "CgroupnsMode": "private",
            "GroupAdd": None, "DeviceRequests": None,
            "DeviceCgroupRules": None, "Runtime": "runc",
            "Sysctls": None, "CgroupParent": "",
            "PublishAllPorts": False, "PortBindings": {},
            "Links": None, "ExtraHosts": None,
            "Binds": None,
            "Mounts": [] if collector else [{
            "Type": "volume", "Source": "", "Target": "/app/data", "ReadOnly": False,
            }],
            "VolumesFrom": [f"{SOURCE_CID}:ro"] if collector else None,
        },
        "Mounts": [{
            "Type": "volume", "Name": VOLUME,
            "Source": f"/var/lib/docker/volumes/{VOLUME}/_data",
            "Destination": "/app/data", "Driver": "local",
            "Mode": "ro" if collector else "", "RW": not collector,
            "Propagation": "",
        }],
        "State": {"Status": "exited"},
    }


def _verify(producer: dict | None = None, collector: dict | None = None):
    return _sut().verify_volume_chain(
        producer_inspect_json=json.dumps([producer or _inspect()]).encode(),
        collector_inspect_json=json.dumps([collector or _inspect(collector=True)]).encode(),
        source_cid=SOURCE_CID, collector_cid=COLLECTOR_CID,
        candidate_sha=SHA, candidate_tree_sha=TREE, image_id=IMAGE,
        run_id=RUN_ID, run_attempt=RUN_ATTEMPT, role=ROLE,
    )


def test_exact_cids_and_labels_allow_only_own_uid1000_anonymous_volume() -> None:
    receipt = _verify()
    assert receipt == {
        "schema_version": 1,
        "status": "OFFHOST_CONTRACT_ONLY",
        "candidate_sha": SHA,
        "candidate_tree_sha": TREE,
        "image_id": IMAGE,
        "run_id": RUN_ID,
        "run_attempt": RUN_ATTEMPT,
        "role": ROLE,
        "source_cid": SOURCE_CID,
        "collector_cid": COLLECTOR_CID,
        "volume_name": VOLUME,
        "mount_target": "/app/data",
        "uid": 1000,
        "gid": 1000,
        "host_or_runtime_verified": False,
    }


@pytest.mark.parametrize("field,default", (
    ("IpcMode", ""), ("IpcMode", "private"),
    ("UTSMode", ""), ("UTSMode", "private"),
    ("PidMode", ""), ("PidMode", "private"),
    ("UsernsMode", ""), ("CgroupnsMode", ""),
    ("CgroupnsMode", "private"),
    ("GroupAdd", None), ("GroupAdd", []),
    ("DeviceRequests", None), ("DeviceRequests", []),
    ("DeviceCgroupRules", None), ("DeviceCgroupRules", []),
    ("Runtime", "runc"), ("Runtime", ""),
    ("Sysctls", None), ("Sysctls", {}),
    ("CgroupParent", ""), ("CgroupParent", None),
    ("PublishAllPorts", False), ("PortBindings", None),
    ("PortBindings", {}), ("Links", None), ("Links", []),
    ("ExtraHosts", None), ("ExtraHosts", []),
))
def test_safe_docker24_hostconfig_defaults_remain_accepted(field, default) -> None:
    producer, collector = _inspect(), _inspect(collector=True)
    producer["HostConfig"][field] = default
    collector["HostConfig"][field] = default
    assert _verify(producer, collector)["status"] == "OFFHOST_CONTRACT_ONLY"


@pytest.mark.parametrize("case", (
    "optional_neutral_mount_keys", "read_only_false_omitted",
    "default_volume_options",
))
def test_docker24_neutral_mount_serialization_remains_accepted(case: str) -> None:
    producer = _inspect()
    declared = producer["HostConfig"]["Mounts"][0]
    if case == "optional_neutral_mount_keys":
        declared.update({"Consistency": "", "BindOptions": None,
                         "VolumeOptions": None, "TmpfsOptions": None})
    elif case == "read_only_false_omitted":
        declared.pop("ReadOnly")
    elif case == "default_volume_options":
        declared["VolumeOptions"] = {
            "NoCopy": False, "Labels": None, "DriverConfig": None,
        }
    assert _verify(producer, _inspect(collector=True))["status"] == "OFFHOST_CONTRACT_ONLY"


@pytest.mark.parametrize("case", (
    "forged_source_cid", "forged_collector_cid", "foreign_commit_label",
    "wrong_run_attempt", "foreign_extra_label", "root_user",
    "host_bind_mount", "extra_mount", "unknown_volume",
    "wrong_collector_source", "collector_rw", "producer_ro", "volume_named_not_anonymous",
    "privileged", "network_enabled", "rootfs_writable",
    "cap_added", "host_pid_namespace", "device_mapped", "tmpfs_mounted",
    "ipc_host", "ipc_other_container", "uts_host", "uts_other_container",
    "pid_other_container", "userns_host", "cgroupns_host",
    "root_supplemental_group", "unexpected_supplemental_group",
    "nvidia_device_request", "device_cgroup_rule", "custom_runtime",
    "host_sysctls", "host_cgroup_parent",
    "mount_unknown_field", "mount_nondefault_consistency",
    "mount_bind_options", "mount_volume_nocopy",
    "mount_volume_driver", "mount_volume_subpath",
    "mount_tmpfs_options", "mount_readonly_zero_int",
    "seccomp_unconfined", "publish_all_ports", "host_port_bindings",
    "link_other_container", "host_gateway_alias", "publish_port_zero_int",
))
def test_foreign_or_privileged_docker_identity_is_hold79(case: str) -> None:
    producer, collector = _inspect(), _inspect(collector=True)
    if case == "forged_source_cid":
        producer["Id"] = "f" * 64
    elif case == "forged_collector_cid":
        collector["Id"] = "f" * 64
    elif case == "foreign_commit_label":
        producer["Config"]["Labels"]["porota.commit"] = "f" * 40
    elif case == "wrong_run_attempt":
        collector["Config"]["Labels"]["porota.run_attempt"] = "2"
    elif case == "foreign_extra_label":
        producer["Config"]["Labels"]["porota.foreign"] = "untrusted"
    elif case == "root_user":
        producer["Config"]["User"] = "0:0"
    elif case == "host_bind_mount":
        producer["Mounts"][0]["Type"] = "bind"
    elif case == "extra_mount":
        collector["Mounts"].append({**collector["Mounts"][0], "Destination": "/other"})
    elif case == "unknown_volume":
        collector["Mounts"][0]["Name"] = "f" * 64
    elif case == "wrong_collector_source":
        collector["HostConfig"]["VolumesFrom"] = ["f" * 64 + ":ro"]
    elif case == "collector_rw":
        collector["Mounts"][0]["RW"] = True
    elif case == "producer_ro":
        producer["Mounts"][0]["RW"] = False
    elif case == "volume_named_not_anonymous":
        producer["HostConfig"]["Mounts"][0]["Source"] = "shared-volume"
    elif case == "privileged":
        producer["HostConfig"]["Privileged"] = True
    elif case == "network_enabled":
        collector["HostConfig"]["NetworkMode"] = "bridge"
    elif case == "rootfs_writable":
        collector["HostConfig"]["ReadonlyRootfs"] = False
    elif case == "cap_added":
        producer["HostConfig"]["CapAdd"] = ["SYS_ADMIN"]
    elif case == "host_pid_namespace":
        collector["HostConfig"]["PidMode"] = "host"
    elif case == "device_mapped":
        producer["HostConfig"]["Devices"] = [{"PathOnHost": "/dev/mem"}]
    elif case == "tmpfs_mounted":
        collector["HostConfig"]["Tmpfs"] = {"/tmp": "rw"}
    elif case == "ipc_host":
        producer["HostConfig"]["IpcMode"] = "host"
    elif case == "ipc_other_container":
        producer["HostConfig"]["IpcMode"] = f"container:{COLLECTOR_CID}"
    elif case == "uts_host":
        collector["HostConfig"]["UTSMode"] = "host"
    elif case == "uts_other_container":
        collector["HostConfig"]["UTSMode"] = f"container:{SOURCE_CID}"
    elif case == "pid_other_container":
        producer["HostConfig"]["PidMode"] = f"container:{COLLECTOR_CID}"
    elif case == "userns_host":
        producer["HostConfig"]["UsernsMode"] = "host"
    elif case == "cgroupns_host":
        collector["HostConfig"]["CgroupnsMode"] = "host"
    elif case == "root_supplemental_group":
        collector["HostConfig"]["GroupAdd"] = ["0"]
    elif case == "unexpected_supplemental_group":
        producer["HostConfig"]["GroupAdd"] = ["sudo"]
    elif case == "nvidia_device_request":
        producer["HostConfig"]["DeviceRequests"] = [{
            "Driver": "nvidia", "Count": -1,
            "Capabilities": [["gpu"]],
        }]
    elif case == "device_cgroup_rule":
        collector["HostConfig"]["DeviceCgroupRules"] = ["a *:* rwm"]
    elif case == "custom_runtime":
        producer["HostConfig"]["Runtime"] = "nvidia"
    elif case == "host_sysctls":
        collector["HostConfig"]["Sysctls"] = {"net.ipv4.ip_forward": "1"}
    elif case == "host_cgroup_parent":
        producer["HostConfig"]["CgroupParent"] = "/host"
    elif case == "mount_unknown_field":
        producer["HostConfig"]["Mounts"][0]["Unexpected"] = "opaque"
    elif case == "mount_nondefault_consistency":
        producer["HostConfig"]["Mounts"][0]["Consistency"] = "delegated"
    elif case == "mount_bind_options":
        producer["HostConfig"]["Mounts"][0]["BindOptions"] = {"Propagation": "rshared"}
    elif case == "mount_volume_nocopy":
        producer["HostConfig"]["Mounts"][0]["VolumeOptions"] = {"NoCopy": True}
    elif case == "mount_volume_driver":
        producer["HostConfig"]["Mounts"][0]["VolumeOptions"] = {
            "DriverConfig": {"Name": "external"}}
    elif case == "mount_volume_subpath":
        producer["HostConfig"]["Mounts"][0]["VolumeOptions"] = {"Subpath": "elsewhere"}
    elif case == "mount_tmpfs_options":
        producer["HostConfig"]["Mounts"][0]["TmpfsOptions"] = {"SizeBytes": 1048576}
    elif case == "mount_readonly_zero_int":
        producer["HostConfig"]["Mounts"][0]["ReadOnly"] = 0
    elif case == "seccomp_unconfined":
        collector["HostConfig"]["SecurityOpt"] = [
            "no-new-privileges", "seccomp=unconfined"]
    elif case == "publish_all_ports":
        producer["HostConfig"]["PublishAllPorts"] = True
    elif case == "publish_port_zero_int":
        producer["HostConfig"]["PublishAllPorts"] = 0
    elif case == "host_port_bindings":
        producer["HostConfig"]["PortBindings"] = {
            "80/tcp": [{"HostIP": "0.0.0.0", "HostPort": "80"}]}
    elif case == "link_other_container":
        collector["HostConfig"]["Links"] = [f"{SOURCE_CID}:/other"]
    elif case == "host_gateway_alias":
        collector["HostConfig"]["ExtraHosts"] = ["host.docker.internal:host-gateway"]
    else:
        raise AssertionError(case)
    with pytest.raises(_sut().VolumeProofHold, match="HOLD79"):
        _verify(producer, collector)


def test_duplicate_docker_inspect_keys_are_rejected_not_last_wins() -> None:
    payload = json.dumps([_inspect()]).encode()
    payload = payload.replace(b'"Id": ', b'"Id": "' + b"f" * 64 + b'", "Id": ', 1)
    with pytest.raises(_sut().VolumeProofHold, match="DUPLICATE_JSON_KEY"):
        _sut().verify_volume_chain(
            producer_inspect_json=payload,
            collector_inspect_json=json.dumps([_inspect(collector=True)]).encode(),
            source_cid=SOURCE_CID, collector_cid=COLLECTOR_CID,
            candidate_sha=SHA, candidate_tree_sha=TREE, image_id=IMAGE, run_id=RUN_ID,
            run_attempt=RUN_ATTEMPT, role=ROLE,
        )


def _tar_bytes(*, replacement: dict[str, bytes] | None = None,
               extra: tuple[str, bytes] | None = None,
               malicious_link: tuple[str, str] | None = None,
               malicious_link_type: bytes = tarfile.SYMTYPE,
               extra_directory: str | None = None,
               pax_header: bool = False,
               hidden_linkname: bool = False,
               duplicate: str | None = None,
               uid: int = 1000, mode: int = 0o600,
               omit: str | None = None) -> bytes:
    payloads = {name: f"receipt:{name}".encode() for name in FILES}
    payloads["native-bound.xml"] = b"hello"
    payloads.update(replacement or {})
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w",
                      format=tarfile.PAX_FORMAT if pax_header else tarfile.USTAR_FORMAT) as archive:
        for name, payload in (*payloads.items(), *((extra,) if extra else ())):
            if name == omit:
                continue
            info = tarfile.TarInfo("./" + name)
            info.uid = uid
            info.gid = uid
            info.mode = mode
            info.size = len(payload)
            if pax_header and name == "native-bound.xml":
                info.pax_headers = {"comment": "unexpected extended metadata"}
            if hidden_linkname and name == "native-bound.xml":
                info.linkname = "foreign.txt"
            archive.addfile(info, io.BytesIO(payload))
        if malicious_link:
            name, target = malicious_link
            info = tarfile.TarInfo("./" + name)
            info.uid = uid
            info.gid = uid
            info.type = malicious_link_type
            info.linkname = target
            archive.addfile(info)
        if extra_directory:
            info = tarfile.TarInfo("./" + extra_directory)
            info.type = tarfile.DIRTYPE
            info.uid = uid
            info.gid = uid
            info.mode = 0o700
            archive.addfile(info)
        if duplicate:
            payload = b"duplicate"
            info = tarfile.TarInfo("./" + duplicate)
            info.uid = uid
            info.gid = uid
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return stream.getvalue()


def test_collector_tar_seals_only_13_native_files_with_exact_digests() -> None:
    proof = _sut()
    raw = _tar_bytes()
    manifest = proof.attest_collector_tar(raw, custody_receipt=_verify())
    assert manifest["status"] == "OFFHOST_BYTES_ONLY"
    assert manifest["file_count"] == 13
    assert manifest["tar_sha256"] == hashlib.sha256(raw).hexdigest()
    assert manifest["tar_size_bytes"] == len(raw)
    assert manifest["candidate_tree_sha"] == TREE
    assert manifest["source_cid"] == SOURCE_CID
    assert manifest["collector_cid"] == COLLECTOR_CID
    assert manifest["volume_name"] == VOLUME
    assert manifest["host_or_runtime_verified"] is False
    assert [row["path"] for row in manifest["files"]] == sorted(FILES)
    native = next(row for row in manifest["files"] if row["path"] == "native-bound.xml")
    assert native == {
        "path": "native-bound.xml", "size_bytes": 5,
        "sha256": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
    }
    assert proof.verify_collector_manifest(
        raw, json.dumps(manifest).encode(),
        expected_candidate_sha=SHA, expected_candidate_tree_sha=TREE,
        expected_run_id=RUN_ID, expected_run_attempt=RUN_ATTEMPT,
        expected_source_cid=SOURCE_CID, expected_collector_cid=COLLECTOR_CID,
        expected_volume_name=VOLUME, expected_image_id=IMAGE,
        expected_role=ROLE,
    ) == manifest


@pytest.mark.parametrize("case", (
    "symlink", "path_traversal", "absolute_path", "extra_file",
    "missing_file", "duplicate_file", "root_owned", "world_writable",
    "truncated", "trailing_garbage", "directory_entry", "hardlink",
    "pax_header", "hidden_linkname",
))
def test_collector_tar_rejects_unsafe_or_incomplete_evidence(case: str) -> None:
    kwargs = {}
    if case == "symlink":
        kwargs["malicious_link"] = ("session/foreign.json", "../../foreign")
    elif case == "hardlink":
        kwargs["malicious_link"] = ("session/foreign.json", "native-bound.xml")
        kwargs["malicious_link_type"] = tarfile.LNKTYPE
    elif case == "path_traversal":
        kwargs["extra"] = ("../foreign.txt", b"foreign")
    elif case == "absolute_path":
        kwargs["extra"] = ("/etc/passwd", b"foreign")
    elif case == "extra_file":
        kwargs["extra"] = ("session/other-app.json", b"foreign")
    elif case == "missing_file":
        kwargs["omit"] = "session/stress.json"
    elif case == "duplicate_file":
        kwargs["duplicate"] = "native-bound.xml"
    elif case == "root_owned":
        kwargs["uid"] = 0
    elif case == "world_writable":
        kwargs["mode"] = 0o666
    elif case == "directory_entry":
        kwargs["extra_directory"] = "session"
    elif case == "pax_header":
        kwargs["pax_header"] = True
    elif case == "hidden_linkname":
        kwargs["hidden_linkname"] = True
    raw = _tar_bytes(**kwargs)
    if case == "truncated":
        raw = raw[:512]
    elif case == "trailing_garbage":
        raw = raw[:-512] + b"A" + bytes(511)
    with pytest.raises(_sut().VolumeProofHold, match="HOLD79"):
        _sut().attest_collector_tar(raw, custody_receipt=_verify())


def test_downloaded_tar_mutation_cannot_reuse_earlier_manifest() -> None:
    original = _tar_bytes()
    manifest = _sut().attest_collector_tar(original, custody_receipt=_verify())
    changed = _tar_bytes(replacement={"native-bound.xml": b"HELLO"})
    assert len(original) == len(changed)
    with pytest.raises(_sut().VolumeProofHold, match="MANIFEST_BYTES_MISMATCH"):
        _sut().verify_collector_manifest(
            changed, json.dumps(manifest).encode(),
            expected_candidate_sha=SHA, expected_candidate_tree_sha=TREE,
            expected_run_id=RUN_ID, expected_run_attempt=RUN_ATTEMPT,
            expected_source_cid=SOURCE_CID, expected_collector_cid=COLLECTOR_CID,
            expected_volume_name=VOLUME, expected_image_id=IMAGE,
            expected_role=ROLE,
        )


def test_untrusted_manifest_cannot_self_attest_another_candidate_tree() -> None:
    raw = _tar_bytes()
    manifest = _sut().attest_collector_tar(raw, custody_receipt=_verify())
    manifest["candidate_tree_sha"] = "e" * 40
    with pytest.raises(_sut().VolumeProofHold, match="MANIFEST_TRUSTED_IDENTITY"):
        _sut().verify_collector_manifest(
            raw, json.dumps(manifest).encode(),
            expected_candidate_sha=SHA, expected_candidate_tree_sha=TREE,
            expected_run_id=RUN_ID, expected_run_attempt=RUN_ATTEMPT,
            expected_source_cid=SOURCE_CID, expected_collector_cid=COLLECTOR_CID,
            expected_volume_name=VOLUME, expected_image_id=IMAGE,
            expected_role=ROLE,
        )


def test_untrusted_manifest_cannot_relabel_proof_role() -> None:
    raw = _tar_bytes()
    manifest = _sut().attest_collector_tar(raw, custody_receipt=_verify())
    manifest["role"] = "foreign"
    with pytest.raises(_sut().VolumeProofHold, match="MANIFEST_TRUSTED_IDENTITY"):
        _sut().verify_collector_manifest(
            raw, json.dumps(manifest).encode(),
            expected_candidate_sha=SHA, expected_candidate_tree_sha=TREE,
            expected_run_id=RUN_ID, expected_run_attempt=RUN_ATTEMPT,
            expected_source_cid=SOURCE_CID, expected_collector_cid=COLLECTOR_CID,
            expected_volume_name=VOLUME, expected_image_id=IMAGE,
            expected_role=ROLE,
        )


def test_manifest_canonical_serialization_is_stable_and_has_no_artifact_id() -> None:
    manifest = _sut().attest_collector_tar(_tar_bytes(), custody_receipt=_verify())
    assert set(manifest) == {
        "schema", "schema_version", "status", "candidate_sha",
        "candidate_tree_sha", "image_id", "run_id", "run_attempt",
        "role", "source_cid", "collector_cid", "volume_name",
        "tar_sha256", "tar_size_bytes", "file_count", "files",
        "host_or_runtime_verified",
    }
    expected = (json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True, allow_nan=False) + "\n").encode()
    assert _sut().canonical_manifest_bytes(manifest) == expected
    assert _sut().canonical_manifest_bytes(dict(reversed(list(manifest.items())))) == expected


def _proof_directory(root: Path) -> Path:
    root.mkdir()
    for relative in FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"receipt:{relative}".encode())
        path.chmod(0o600)
    (root / "native-bound.xml").write_bytes(b"hello")
    return root


def test_collector_pack_uses_only_fixed_regular_uid1000_files(tmp_path: Path) -> None:
    root = _proof_directory(tmp_path / "volume")
    (root / "logs").mkdir()
    (root / "logs" / "host-private.log").write_bytes(b"NOT_EVIDENCE")
    packed = _sut().pack_collector_tar(str(root))
    assert packed == _sut().pack_collector_tar(str(root))
    manifest = _sut().attest_collector_tar(packed, custody_receipt=_verify())
    assert manifest["file_count"] == 13
    assert [row["path"] for row in manifest["files"]] == sorted(FILES)
    assert b"NOT_EVIDENCE" not in packed


@pytest.mark.parametrize("case", ("missing", "symlink", "unsafe_mode", "root_symlink"))
def test_collector_pack_fails_closed_on_unsafe_volume_path(case: str, tmp_path: Path) -> None:
    root = _proof_directory(tmp_path / "volume")
    native = root / "native-bound.xml"
    if case == "missing":
        native.unlink()
    elif case == "symlink":
        native.unlink()
        native.symlink_to("../foreign.xml")
    elif case == "unsafe_mode":
        native.chmod(0o666)
    elif case == "root_symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(root, target_is_directory=True)
        root = alias
    with pytest.raises(_sut().VolumeProofHold, match="HOLD79"):
        _sut().pack_collector_tar(str(root))


def test_collector_pack_rejects_root_identity_even_with_readable_volume(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _proof_directory(tmp_path / "volume")
    monkeypatch.setattr(_sut().os, "geteuid", lambda: 0)
    with pytest.raises(_sut().VolumeProofHold, match="PACK_UID_INVALID"):
        _sut().pack_collector_tar(str(root))


def _cli(*args: str) -> subprocess.CompletedProcess[bytes]:
    script = Path(__file__).parents[1] / "scripts/rc6_r163_native_volume_proof.py"
    return subprocess.run([sys.executable, str(script), *args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          check=False, timeout=15)


def test_cli_pack_inspect_attest_verify_entire_offhost_chain(tmp_path: Path) -> None:
    root = _proof_directory(tmp_path / "volume")
    packed = _cli("pack", "--proof-root", str(root))
    assert packed.returncode == 0, packed.stderr
    tar_path = tmp_path / "native.tar"
    tar_path.write_bytes(packed.stdout)
    source_inspect = tmp_path / "producer.json"
    source_inspect.write_text(json.dumps([_inspect()]))
    collector_inspect = tmp_path / "collector.json"
    collector_inspect.write_text(json.dumps([_inspect(collector=True)]))
    inspect = _cli(
        "inspect", "--producer-inspect", str(source_inspect),
        "--collector-inspect", str(collector_inspect),
        "--source-cid", SOURCE_CID, "--collector-cid", COLLECTOR_CID,
        "--candidate-sha", SHA, "--candidate-tree-sha", TREE,
        "--image-id", IMAGE, "--run-id", RUN_ID,
        "--run-attempt", RUN_ATTEMPT, "--role", ROLE,
    )
    assert inspect.returncode == 0, inspect.stderr
    receipt = json.loads(inspect.stdout)
    assert receipt == _verify()
    custody_path = tmp_path / "custody.json"
    custody_path.write_bytes(inspect.stdout)
    attest = _cli("attest", "--tar", str(tar_path),
                  "--custody", str(custody_path))
    assert attest.returncode == 0, attest.stderr
    manifest = json.loads(attest.stdout)
    assert manifest == _sut().attest_collector_tar(packed.stdout, custody_receipt=receipt)
    assert attest.stdout == _sut().canonical_manifest_bytes(manifest)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(attest.stdout)
    expected_args = (
        "--expected-candidate-sha", SHA,
        "--expected-candidate-tree-sha", TREE,
        "--expected-run-id", RUN_ID,
        "--expected-run-attempt", RUN_ATTEMPT,
        "--expected-source-cid", SOURCE_CID,
        "--expected-collector-cid", COLLECTOR_CID,
        "--expected-volume-name", VOLUME,
        "--expected-image-id", IMAGE,
        "--expected-role", ROLE,
    )
    verified = _cli("verify", "--tar", str(tar_path),
                    "--manifest", str(manifest_path), *expected_args)
    assert verified.returncode == 0, verified.stderr
    assert verified.stdout == attest.stdout
    bad = _cli("verify", "--tar", str(tar_path),
               "--manifest", str(manifest_path),
               *expected_args[:-2], "--expected-role", "foreign")
    assert bad.returncode == 79
    assert bad.stdout == b""
    assert bad.stderr == b"RC6_R163_HOLD79:MANIFEST_TRUSTED_IDENTITY\n"


def test_cli_forged_inspect_cid_returns_bounded_hold79_not_payload(tmp_path: Path) -> None:
    producer = tmp_path / "producer.json"
    producer.write_bytes(json.dumps([_inspect()]).encode())
    collector = tmp_path / "collector.json"
    collector.write_bytes(json.dumps([_inspect(collector=True)]).encode())
    result = _cli(
        "inspect", "--producer-inspect", str(producer),
        "--collector-inspect", str(collector),
        "--source-cid", "f" * 64, "--collector-cid", COLLECTOR_CID,
        "--candidate-sha", SHA, "--candidate-tree-sha", TREE,
        "--image-id", IMAGE, "--run-id", RUN_ID,
        "--run-attempt", RUN_ATTEMPT, "--role", ROLE,
    )
    assert result.returncode == 79
    assert result.stdout == b""
    assert result.stderr == b"RC6_R163_HOLD79:CID_IMAGE_IDENTITY\n"
