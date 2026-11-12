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
import zipfile

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
            "Mode": "" if collector else "z", "RW": not collector,
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


@pytest.mark.parametrize("role,physical_mode", (
    ("producer", "z"),
    ("collector", ""),
))
def test_r186_physical_linux_docker28_mode_semantics(
    role: str, physical_mode: str,
) -> None:
    """Mode is a Docker mount label; read-only authority is the separate RW bit.

    Observed on GitHub-hosted Engine 28.0.4, run 43142337952.
    This is initially RED against the old literal producer-empty/collector-ro gate.
    """
    producer = _inspect()
    collector = _inspect(collector=True)
    selected = producer if role == "producer" else collector
    selected["Mounts"][0]["Mode"] = physical_mode
    result = _verify(producer, collector)
    assert result["volume_name"] == VOLUME


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
    "anonymous_source_omitted", "default_volume_options",
))
def test_docker24_neutral_mount_serialization_remains_accepted(case: str) -> None:
    producer = _inspect()
    declared = producer["HostConfig"]["Mounts"][0]
    if case == "optional_neutral_mount_keys":
        declared.update({"Consistency": "", "BindOptions": None,
                         "VolumeOptions": None, "TmpfsOptions": None})
    elif case == "read_only_false_omitted":
        declared.pop("ReadOnly")
    elif case == "anonymous_source_omitted":
        declared.pop("Source")
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
    "mount_source_null", "mount_source_numeric", "mount_source_list",
    "mount_source_64hex",
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
    elif case == "mount_source_null":
        producer["HostConfig"]["Mounts"][0]["Source"] = None
    elif case == "mount_source_numeric":
        producer["HostConfig"]["Mounts"][0]["Source"] = 0
    elif case == "mount_source_list":
        producer["HostConfig"]["Mounts"][0]["Source"] = []
    elif case == "mount_source_64hex":
        producer["HostConfig"]["Mounts"][0]["Source"] = VOLUME
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



@pytest.mark.parametrize("collector,field,value,bad_check", (
    (False, "Type", "bind", "type"),
    (False, "Destination", "/private/do-not-print", "target"),
    (False, "Name", "secret-volume-do-not-print", "name"),
    (False, "Source", "", "source"),
    (False, "Driver", "private-driver-do-not-print", "driver"),
    (False, "RW", False, "rw"),
    (False, "Mode", "rw", "mode"),
    (False, "Mode", "", "mode"),
    (False, "Mode", None, "mode"),
    (False, "Mode", "z\\n/private/never-log", "mode"),
    (True, "Type", "bind", "type"),
    (True, "Destination", "/private/do-not-print", "target"),
    (True, "Name", "secret-volume-do-not-print", "name"),
    (True, "Source", "", "source"),
    (True, "Driver", "private-driver-do-not-print", "driver"),
    (True, "RW", True, "rw"),
    (True, "Mode", "rw", "mode"),
    (True, "Mode", "ro", "mode"),
    (True, "Mode", "z", "mode"),
    (True, "Mode", None, "mode"),
))
def test_mount_hold79_reports_only_boolean_subchecks(
    collector: bool, field: str, value: object, bad_check: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    producer = _inspect()
    reader = _inspect(collector=True)
    target = reader if collector else producer
    target["Mounts"][0][field] = value
    with pytest.raises(_sut().VolumeProofHold, match="HOLD79:MOUNT_VOLUME_IDENTITY"):
        _verify(producer, reader)
    reported = capsys.readouterr()
    assert reported.out == ""
    role = "collector" if collector else "producer"
    prefix = "RC6_R163_MOUNT_SUBCHECKS=role=" + role + "|"
    assert reported.err.startswith(prefix)
    assert reported.err.count("\n") == 1
    details = reported.err[len(prefix):].strip().split("|")
    assert details == [
        name + "=" + str(int(name != bad_check))
        for name in ("type", "target", "name", "source", "driver", "rw", "mode")
    ]
    for sensitive in (
        SOURCE_CID, COLLECTOR_CID, VOLUME, SHA, TREE,
        "/var/lib/docker/", "secret-volume-do-not-print",
        "/private/do-not-print", "private-driver-do-not-print",
    ):
        assert sensitive not in reported.err


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
        path.write_bytes(_r206_private_fixture(relative))
        path.chmod(0o600)
    return root


def test_collector_pack_uses_only_fixed_regular_uid1000_files(tmp_path: Path) -> None:
    root = _proof_directory(tmp_path / "volume")
    (root / "logs").mkdir()
    (root / "logs" / "host-private.log").write_bytes(b"NOT_EVIDENCE")
    private_before = {rel: (root / rel).read_bytes() for rel in FILES}
    packed = _sut().pack_collector_tar(str(root))
    assert packed == _sut().pack_collector_tar(str(root))
    assert {rel: (root / rel).read_bytes() for rel in FILES} == private_before
    assert _sut().verify_native_tar_privacy(packed) == sorted(FILES)
    assert b"original-not-public" not in packed
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


# R203: all assertions here are offline; no Docker, host, network or secrets.
def _public_identity_args() -> dict:
    return {
        "source_cid": SOURCE_CID, "collector_cid": COLLECTOR_CID,
        "candidate_sha": SHA, "candidate_tree_sha": TREE,
        "image_id": IMAGE, "run_id": RUN_ID,
        "run_attempt": RUN_ATTEMPT, "role": ROLE,
    }


def _public_pair(producer: dict | None = None,
                 collector: dict | None = None) -> tuple[bytes, bytes]:
    source = _inspect() if producer is None else producer
    reader = _inspect(collector=True) if collector is None else collector
    return _sut().project_public_inspects_v2(
        producer_inspect_json=json.dumps([source]).encode(),
        collector_inspect_json=json.dumps([reader]).encode(),
        **_public_identity_args(),
    )


def test_r203_public_inspect_v2_exact_allowlist_and_raw_commitments() -> None:
    producer, collector = _inspect(), _inspect(collector=True)
    # Deliberately include realistic private-only fields that must never
    # appear in the public JSON or in the fixed stdout/stderr diagnostics.
    private = "PRIVATE_PROOF_CANARY_DO_NOT_PUBLISH"
    for entry in (producer, collector):
        entry["Config"]["Env"] = ["EXAMPLE=" + private]
        entry["GraphDriver"] = {"Data": {"MergedDir": "/private/" + private}}
        entry["LogPath"] = "/private/" + private + ".log"
        entry["NetworkSettings"] = {"IPAddress": private}
        entry["HostConfig"]["UnknownPrivateField"] = private
        entry["Mounts"][0]["Source"] += "/" + private
    raw_source = json.dumps([producer]).encode()
    raw_collector = json.dumps([collector]).encode()
    source, reader = _sut().project_public_inspects_v2(
        producer_inspect_json=raw_source, collector_inspect_json=raw_collector,
        **_public_identity_args(),
    )
    assert b"PRIVATE_PROOF_CANARY_DO_NOT_PUBLISH" not in source + reader
    assert b"GraphDriver" not in source + reader
    assert b"LogPath" not in source + reader
    assert b"NetworkSettings" not in source + reader
    assert b'"Source"' not in source + reader
    assert b'"Env"' not in source + reader
    assert source.endswith(b"\n") and reader.endswith(b"\n")
    assert source == (json.dumps(json.loads(source), sort_keys=True,
                                 separators=(",", ":"), ensure_ascii=True) + "\n").encode()
    for payload, raw, expected_role in (
        (source, raw_source, "bundle"), (reader, raw_collector, "bundle-collector"),
    ):
        doc = json.loads(payload)
        assert set(doc) == {
            "schema", "schema_version", "role", "status", "raw_inspect",
            "source_redaction", "record", "host_or_runtime_verified",
        }
        assert doc["schema"] == "RC6_R163_PUBLIC_INSPECT_V2"
        assert doc["schema_version"] == 2
        assert doc["role"] == expected_role
        assert doc["status"] == "OFFHOST_PROJECTED_EVIDENCE_ONLY"
        assert doc["host_or_runtime_verified"] is False
        assert doc["raw_inspect"] == {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw), "published": False,
        }
        assert set(doc["record"]) == {"Id", "Image", "Config", "HostConfig", "Mounts"}
        assert set(doc["record"]["Config"]) == {"User", "Labels"}
        assert set(doc["record"]["Mounts"][0]) == {
            "Type", "Name", "Driver", "Destination", "Mode", "RW",
        }
    assert _sut().verify_public_inspects_v2(
        producer_public_json=source, collector_public_json=reader,
        **_public_identity_args(),
    ) == _verify()


@pytest.mark.parametrize("declaration", ("ABSENT", "EMPTY"))
def test_r203_source_declaration_redaction_is_explicit(declaration: str) -> None:
    producer = _inspect()
    if declaration == "ABSENT":
        del producer["HostConfig"]["Mounts"][0]["Source"]
    source, reader = _public_pair(producer)
    assert json.loads(source)["source_redaction"] == {
        "hostconfig_declaration": declaration,
        "runtime_source_nonempty_observed": True,
    }
    assert json.loads(reader)["source_redaction"] == {
        "hostconfig_declaration": "NOT_APPLICABLE",
        "runtime_source_nonempty_observed": True,
    }
    assert _sut().verify_public_inspects_v2(
        producer_public_json=source, collector_public_json=reader,
        **_public_identity_args(),
    )["volume_name"] == VOLUME


@pytest.mark.parametrize("case", (
    "outer_env", "outer_private_path", "wrong_version", "faked_role",
    "fake_raw_digest", "raw_published", "raw_zero_size", "redaction_hidden",
    "unobserved_source", "record_private_env", "hostconfig_unknown",
    "hostconfig_declared_source", "runtime_mount_source",
    "runtime_mode", "runtime_rw", "forged_cid", "foreign_label",
    "host_namespace", "collector_wrong_source",
))
def test_r203_public_inspect_v2_forgery_is_hold79_not_host_proof(
    case: str, capsys: pytest.CaptureFixture[str],
) -> None:
    source, reader = _public_pair()
    docs = [json.loads(source), json.loads(reader)]
    selected = docs[0] if case not in ("collector_wrong_source",) else docs[1]
    record = selected["record"]
    if case == "outer_env":
        selected["Env"] = ["CANARY_SECRET"]
    elif case == "outer_private_path":
        selected["GraphDriver"] = {"Data": "CANARY_SECRET"}
    elif case == "wrong_version":
        selected["schema_version"] = 1
    elif case == "faked_role":
        selected["role"] = "bundle"
        selected = docs[1]
        selected["role"] = "bundle"
    elif case == "fake_raw_digest":
        selected["raw_inspect"]["sha256"] = "not-a-digest"
    elif case == "raw_published":
        selected["raw_inspect"]["published"] = True
    elif case == "raw_zero_size":
        selected["raw_inspect"]["size_bytes"] = 0
    elif case == "redaction_hidden":
        selected["source_redaction"]["hostconfig_declaration"] = "NAMED"
    elif case == "unobserved_source":
        selected["source_redaction"]["runtime_source_nonempty_observed"] = False
    elif case == "record_private_env":
        record["Config"]["Env"] = ["CANARY_SECRET"]
    elif case == "hostconfig_unknown":
        record["HostConfig"]["RuntimeSecret"] = "CANARY_SECRET"
    elif case == "hostconfig_declared_source":
        record["HostConfig"]["Mounts"][0]["Source"] = "CANARY_SECRET"
    elif case == "runtime_mount_source":
        record["Mounts"][0]["Source"] = "/private/CANARY_SECRET"
    elif case == "runtime_mode":
        record["Mounts"][0]["Mode"] = "rw"
    elif case == "runtime_rw":
        record["Mounts"][0]["RW"] = False
    elif case == "forged_cid":
        record["Id"] = "9" * 64
    elif case == "foreign_label":
        record["Config"]["Labels"]["porota.foreign"] = "CANARY_SECRET"
    elif case == "host_namespace":
        record["HostConfig"]["IpcMode"] = "host"
    elif case == "collector_wrong_source":
        record["HostConfig"]["VolumesFrom"] = ["9" * 64 + ":ro"]
    else:
        raise AssertionError(case)
    with pytest.raises(_sut().VolumeProofHold, match="RC6_R163_HOLD79"):
        _sut().verify_public_inspects_v2(
            producer_public_json=json.dumps(docs[0]).encode(),
            collector_public_json=json.dumps(docs[1]).encode(),
            **_public_identity_args(),
        )
    output = capsys.readouterr()
    assert "CANARY_SECRET" not in output.err + output.out


def _r206_private_fixture(relative: str) -> bytes:
    """Private synthetic source with required PAPER-only shape (never uploaded)."""
    if relative.endswith(".xml"):
        return (b'<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0">'
                b'<testcase name="original-not-public" classname="private.fixture"/>'
                b'</testsuite></testsuites>')
    catalog = {
        "schema": "rc6.synthetic-full-catalog.v1",
        "catalog_count": 14611, "ready_fixture_count": 9902,
        "required_generations": 2, "real_orders_sent": 0,
        "provider_requests": 0, "real_routes": "NOT_CALLED",
        "production_data_mounted": False, "actual_market_acceptance": False,
        "database_unchanged": True, "cuts": [{}, {}],
    }
    if relative == "session/stress.json":
        source = {
            "schema": "rc6.postdeploy-stress.v1", "status": "GREEN",
            "production_data_mounted": False, "ppi_watch_accessed": False,
            "session": {"real_orders_sent": 0, "provider_requests": 0,
                        "real_routes": "NOT_CALLED"},
            "full_catalog": catalog,
            "scenarios": [
                {"name": group, "cases": 1, "junit_sha256": "d" * 64,
                 "exit_code": 0, "failure": 0, "error": 0, "skipped": 0}
                for group in (
                    "sql_shadow_wal", "publication_io_identity",
                    "signals_risk_exit_ledger", "sources_readiness_network",
                    "scalping_capacity", "treasury_caucion", "dashboard_auth_52",
                    "scheduler_monitor_backup",
                )
            ],
        }
    elif relative == "catalog/full-catalog.json":
        source = catalog
    else:
        mode = "fresh" if relative.endswith("fresh.json") else "legacy"
        source = {**catalog, "required_generations": 12,
                  "status": "GREEN", "cadence_mode": mode,
                  "cuts": [{} for _ in range(12)], "observed_publications": 12}
    return (json.dumps(source, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _r206_public_fixture(
        relative: str, *, digest: str = "0" * 64,
        private_bytes: int = 123) -> bytes:
    """Hand-specified PUBLIC contract: no private contents or testcase names."""
    if relative.endswith(".xml"):
        return (
            '<testsuite schema="RC6_R206_PUBLIC_JUNIT_V1" '
            'status="PRIVATE_SOURCE_UNATTESTED" path="' + relative +
            '" name="R206_PUBLIC_SYNTHETIC_ONLY" tests="1" failures="0" '
            'errors="0" skipped="0" private_raw_size_bytes="' + str(private_bytes) + '" '
            'private_raw_sha256_unattested="' + digest +
            '" release_go="false"><testcase name="case-000001"/></testsuite>\n'
        ).encode("ascii")
    record = {
        "schema": "RC6_R206_PUBLIC_JSON_V1",
        "status": "PRIVATE_SOURCE_UNATTESTED",
        "source_path": relative,
        "private_raw_size_bytes": private_bytes,
        "private_raw_sha256_unattested": digest,
        "synthetic_fixture_only": True, "release_go": False,
        "real_orders_sent": 0, "catalog_count": 14611,
        "ready_fixture_count": 9902,
        "required_generations": 12 if relative.startswith("cadence/") else 2,
    }
    return (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _privacy_tar(*, replacement: dict[str, bytes] | None = None) -> bytes:
    payloads = {relative: _r206_public_fixture(relative) for relative in FILES}
    payloads.update(replacement or {})
    return _tar_bytes(replacement=payloads)


@pytest.mark.parametrize(
    "relative", tuple(path for path in FILES if path.endswith(".xml"))
)
@pytest.mark.parametrize(
    "opaque", (
        "AKIAABCDEFGHIJKLMNOP",
        "https://private.example.invalid/opaque/person-reference",
    ),
)
def test_r206_raw_junit_opaque_attribute_is_not_public_evidence(
        relative: str, opaque: str) -> None:
    """No public native TAR may contain raw JUnit testcase attributes."""
    source = (
        '<testsuite tests="1" failures="0" errors="0" skipped="0">'
        f'<testcase name="{opaque}" classname="private.fixture"/>'
        '</testsuite>'
    ).encode("utf-8")
    with pytest.raises(_sut().VolumeProofHold, match="PRIVACY_PUBLIC_XML_SCHEMA"):
        _sut().verify_native_tar_privacy(
            _privacy_tar(replacement={relative: source}))


@pytest.mark.parametrize(
    "relative", tuple(path for path in FILES if path.endswith(".json"))
)
@pytest.mark.parametrize(
    "opaque", (
        "AKIAABCDEFGHIJKLMNOP",
        "https://private.example.invalid/opaque/person-reference",
    ),
)
def test_r206_raw_json_opaque_field_is_not_public_evidence(
        relative: str, opaque: str) -> None:
    """Public JSON is a fixed synthetic projection, never a raw dict."""
    source = json.dumps({"status": "GREEN", "opaque_value": opaque}).encode("utf-8")
    with pytest.raises(_sut().VolumeProofHold, match="PRIVACY_PUBLIC_JSON_SCHEMA"):
        _sut().verify_native_tar_privacy(
            _privacy_tar(replacement={relative: source}))


@pytest.mark.parametrize("relative", FILES)
def test_r206_collector_projects_private_fixture_to_exact_public_schema(
        relative: str) -> None:
    raw = _r206_private_fixture(relative)
    public = _sut()._project_private_native_member(relative, raw)
    assert public == _r206_public_fixture(
        relative, digest=hashlib.sha256(raw).hexdigest(),
        private_bytes=len(raw))
    assert b"original-not-public" not in public
    assert b"private.fixture" not in public
    _sut()._privacy_check_member(relative, public)


@pytest.mark.parametrize("relative", FILES)
def test_r206_public_schema_rejects_tampered_synthetic_bytes(relative: str) -> None:
    canonical = _r206_public_fixture(relative)
    if relative.endswith(".xml"):
        changed = canonical.replace(
            b'name="case-000001"', b'name="private_opaque"')
        reason = "PRIVACY_PUBLIC_XML_SCHEMA"
    else:
        changed = canonical.replace(
            b'"release_go":false', b'"release_go":true')
        reason = "PRIVACY_PUBLIC_JSON_SCHEMA"
    assert changed != canonical
    with pytest.raises(_sut().VolumeProofHold, match=reason):
        _sut().verify_native_tar_privacy(
            _privacy_tar(replacement={relative: changed}))


@pytest.mark.parametrize("raw", (
    b'<testsuite tests="2" failures="0" errors="0" skipped="0">'
    b'<testcase name="one"/></testsuite>',
    b'<testsuite tests="1" failures="1" errors="0" skipped="0">'
    b'<testcase name="one"><failure>opaque</failure></testcase></testsuite>',
    b'<testsuite tests="1" failures="0" errors="0" skipped="0">'
    b'<testcase name="one"><skipped/></testcase></testsuite>',
))
def test_r206_private_junit_invalid_counts_or_negative_nodes_hold(raw: bytes) -> None:
    with pytest.raises(_sut().VolumeProofHold, match="PRIVACY_PRIVATE_JUNIT"):
        _sut()._project_private_native_member("native-bound.xml", raw)


@pytest.mark.parametrize(
    "relative", tuple(path for path in FILES if path.endswith(".json"))
)
def test_r206_private_json_red_or_bool_in_place_of_zero_holds(relative: str) -> None:
    raw = json.loads(_r206_private_fixture(relative))
    if relative == "catalog/full-catalog.json":
        raw["real_orders_sent"] = False  # bool is not an integer zero
    else:
        raw["status"] = "RED"
    with pytest.raises(_sut().VolumeProofHold, match="PRIVACY_PRIVATE_JSON"):
        _sut()._project_private_native_member(
            relative, json.dumps(raw).encode("utf-8"))


@pytest.mark.parametrize("source", (
    b'<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0">'
    b'<testcase name="counted"/></testsuite><testcase name="rogue"/></testsuites>',
    b'<testsuite tests="1" failures="0" errors="0" skipped="0">'
    b'<testcase name="counted"/><testsuite tests="1" failures="0" '
    b'errors="0" skipped="0"><testcase name="nested"/></testsuite></testsuite>',
    b'<testsuite tests="1" failures="0" errors="0" skipped="0">'
    b'<testcase name="counted"/><testsuite tests="0" failures="0" '
    b'errors="0" skipped="0"></testsuite></testsuite>',
))
def test_r206_private_junit_rejects_unaccounted_or_nested_testcase(
        source: bytes) -> None:
    with pytest.raises(_sut().VolumeProofHold, match="PRIVACY_PRIVATE_JUNIT"):
        _sut()._project_private_native_member("native-bound.xml", source)


@pytest.mark.parametrize(
    "relative", tuple(path for path in FILES if path.endswith(".json"))
)
def test_r206_private_json_rejects_nonfinite_exponent_in_discarded_field(
        relative: str) -> None:
    valid = _r206_private_fixture(relative).decode("utf-8").strip()
    assert valid.endswith("}")
    poisoned = (valid[:-1] + ',"ignored_numeric":1e999}').encode("utf-8")
    with pytest.raises(_sut().VolumeProofHold, match="PRIVACY_PRIVATE_JSON"):
        _sut()._project_private_native_member(relative, poisoned)


@pytest.mark.parametrize("mutation", (
    "missing", "duplicate", "reordered", "zero_cases", "bad_digest",
))
def test_r206_stress_requires_complete_ordered_positive_junit_scenarios(
        mutation: str) -> None:
    source = json.loads(_r206_private_fixture("session/stress.json"))
    scenarios = source["scenarios"]
    if mutation == "missing":
        scenarios.pop()
    elif mutation == "duplicate":
        scenarios[1]["name"] = scenarios[0]["name"]
    elif mutation == "reordered":
        scenarios[0], scenarios[1] = scenarios[1], scenarios[0]
    elif mutation == "zero_cases":
        scenarios[2]["cases"] = 0
    else:
        scenarios[3]["junit_sha256"] = "invalid"
    with pytest.raises(_sut().VolumeProofHold, match="PRIVACY_PRIVATE_JSON"):
        _sut()._project_private_native_member(
            "session/stress.json", json.dumps(source).encode("utf-8"))


def test_r203_synthetic_native_privacy_all_thirteen_paths_are_checked() -> None:
    assert _sut().verify_native_tar_privacy(_privacy_tar()) == sorted(FILES)


@pytest.mark.parametrize("relative", FILES)
def test_r203_canary_secret_in_each_native_member_is_hold79(relative: str) -> None:
    # No real credentials, paths, tokens or user data are used as fixtures.
    secret = "CANARY_SECRET_A7F3A2D927F6"
    poisoned = (f'<testsuite tests="1" failures="0" errors="0" skipped="0">'
                f'<testcase name="{secret}"/></testsuite>'.encode()
                if relative.endswith(".xml")
                else json.dumps({"evidence": secret}).encode())
    tar = _privacy_tar(replacement={relative: poisoned})
    with pytest.raises(_sut().VolumeProofHold, match="PRIVACY"):
        _sut().verify_native_tar_privacy(tar)


@pytest.mark.parametrize("payload", (
    b'{"Authorization":"Bearer ghp_fake_fixture_token_1234567890123456"}',
    b'{"api_key":"fake-example-value"}',
    b'{"path":"/var/lib/docker/volumes/private"}',
    b'{"payload":"-----BEGIN PRIVATE KEY-----"}',
))
def test_r203_privacy_rejects_high_signal_synthetic_credentials(payload: bytes) -> None:
    with pytest.raises(_sut().VolumeProofHold, match="PRIVACY"):
        _sut().verify_native_tar_privacy(
            _privacy_tar(replacement={"session/stress.json": payload}))


def test_r203_public_v2_cli_writes_two_new_files_no_raw_stdout(
    tmp_path: Path,
) -> None:
    raw_producer = tmp_path / "producer-raw.json"
    raw_reader = tmp_path / "collector-raw.json"
    raw_producer.write_bytes(json.dumps([_inspect()]).encode())
    raw_reader.write_bytes(json.dumps([_inspect(collector=True)]).encode())
    source_output = tmp_path / "source-public.json"
    reader_output = tmp_path / "collector-public.json"
    argv = (
        "--producer-inspect", str(raw_producer),
        "--collector-inspect", str(raw_reader),
        "--source-cid", SOURCE_CID, "--collector-cid", COLLECTOR_CID,
        "--candidate-sha", SHA, "--candidate-tree-sha", TREE,
        "--image-id", IMAGE, "--run-id", RUN_ID,
        "--run-attempt", RUN_ATTEMPT, "--role", ROLE,
    )
    result = _cli("project", *argv, "--producer-public", str(source_output),
                  "--collector-public", str(reader_output))
    assert result.returncode == 0, result.stderr
    assert result.stdout == b""
    assert source_output.stat().st_mode & 0o077 == 0
    assert reader_output.stat().st_mode & 0o077 == 0
    assert _sut().verify_public_inspects_v2(
        producer_public_json=source_output.read_bytes(),
        collector_public_json=reader_output.read_bytes(),
        **_public_identity_args(),
    ) == _verify()
    repeated = _cli("project", *argv, "--producer-public", str(source_output),
                    "--collector-public", str(reader_output))
    assert repeated.returncode == 79  # O_EXCL; cannot silently overwrite.


def _preview_zip(*, poison: dict[str, bytes] | None = None) -> bytes:
    source, reader = _public_pair()
    tar = _privacy_tar()
    custody = _verify()
    native = _sut().attest_collector_tar(tar, custody_receipt=custody)
    members = {
        "source-inspect.json": source,
        "collector-inspect.json": reader,
        "custody.json": (json.dumps(custody) + "\n").encode(),
        "native.tar": tar,
        "native-manifest.json": _sut().canonical_manifest_bytes(native),
    }
    members.update(poison or {})
    files = [{
        "path": name, "type": "file", "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    } for name, data in sorted(members.items())]
    members["porota-artifact-byte-manifest-v1.json"] = (
        json.dumps({
            "schema_version": 1, "candidate_sha": SHA,
            "candidate_tree_sha": TREE, "run_id": int(RUN_ID),
            "run_attempt": int(RUN_ATTEMPT), "artifact_name": "synthetic-offhost",
            "file_count": len(files),
            "total_size_bytes": sum(row["size_bytes"] for row in files),
            "files": files,
        }, sort_keys=True, separators=(",", ":")) + "\n").encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in sorted(members.items()):
            archive.writestr(name, data)
    return output.getvalue()


def test_r203_public_zip_preview_protects_six_members_without_host_trust() -> None:
    assert _sut().verify_probe_zip_privacy(
        _preview_zip(), **_public_identity_args()) == 13


@pytest.mark.parametrize("member", (
    "source-inspect.json", "collector-inspect.json", "custody.json",
    "native.tar", "native-manifest.json",
))
def test_r203_zip_preview_rejects_canary_in_any_evidence_member(member: str) -> None:
    poisoned = {member: b"CANARY_SECRET_RANDOM_FIXTURE_ONLY"}
    with pytest.raises(_sut().VolumeProofHold, match="PRIVACY"):
        _sut().verify_probe_zip_privacy(
            _preview_zip(poison=poisoned), **_public_identity_args())


def test_r203_zip_preview_rejects_raw_private_docker_inspect() -> None:
    raw = json.dumps([_inspect()]).encode()
    with pytest.raises(_sut().VolumeProofHold, match="HOLD79"):
        _sut().verify_probe_zip_privacy(
            _preview_zip(poison={"source-inspect.json": raw}),
            **_public_identity_args())


def test_r203_live_synthetic_canary_detector_exercises_all_paths() -> None:
    source, reader = _public_pair()
    assert _sut().challenge_privacy_patterns(_privacy_tar(), source, reader) is None




def test_r203_offhost_workflow_never_uploads_private_inspect_or_skips_privacy() -> None:
    workflow = (Path(__file__).parents[1] /
                ".github/workflows/rc6-r163-offhost-guard79-proof.yml").read_text()
    assert 'docker inspect "$source_cid" > "$proof_root/private/source-inspect.json"' in workflow
    assert 'docker inspect "$collector_cid" > "$proof_root/private/collector-inspect.json"' in workflow
    assert 'docker inspect "$source_cid" > "$proof_root/evidence/source-inspect.json"' not in workflow
    assert 'docker inspect "$collector_cid" > "$proof_root/evidence/collector-inspect.json"' not in workflow
    assert "--producer-public" in workflow and "--collector-public" in workflow
    assert "proof.verify_public_inspects_v2(" in workflow
    assert "proof.verify_probe_zip_privacy(" in workflow
    assert "proof.challenge_privacy_patterns(" in workflow
    assert "RC6_R191_PUBLIC_INSPECT=HOLD79" not in workflow
    assert "release_go=NO|runtime_verified=NO" in workflow


@pytest.mark.parametrize("path,payload", (
    ("session/stress.json", b'{"value":"CANARY_\\u0053ECRET_ENCODED"}'),
    ("session/dashboard_auth_52.xml",
     b'<testsuite tests="1"><testcase name="CANARY&#95;SECRET_ENCODED"/></testsuite>'),
))
def test_r203_privacy_rejects_decoded_escape_canary(path: str, payload: bytes) -> None:
    with pytest.raises(_sut().VolumeProofHold, match="PRIVACY"):
        _sut().verify_native_tar_privacy(
            _privacy_tar(replacement={path: payload}))

