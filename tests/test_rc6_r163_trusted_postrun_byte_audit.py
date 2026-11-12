"""Adversarial offline fixtures for a proposed trusted-main postrun byte audit.

These are synthetic REST replies and TAR/ZIP bytes; never a GitHub/runtime GO.
"""

from __future__ import annotations

import hashlib
import base64
import copy
import io
import json
import os
from pathlib import Path
import stat
import struct
import tarfile
import tempfile
import unittest
from unittest import mock
import warnings
import zipfile
import urllib.error

from scripts.rc6_r163_trusted_postrun_byte_audit import (
    GitHubReadOnlyREST,
    PostrunEvidenceRed,
    audit_from_github,
    allowed_storage_redirect,
    load_unique_json,
    verify_postrun_bytes,
)
from scripts import rc6_r163_trusted_postrun_byte_audit as trusted


REPO = "mbalbo2023/Porota-trading"
BASE = "deploy/rc6-pr69-isolated-20260915"
HEAD_REF = "integration/rc6-a1115-r87-unified-paper-candidate-20261110"
SHA = "a" * 40
TREE = "b" * 40
RUN = 991
ATTEMPT = 2
ARTIFACT_ID = 883
SOURCE_CID = "1" * 64
COLLECTOR_CID = "2" * 64
VOLUME = "3" * 64
IMAGE_ID = "sha256:" + "4" * 64
NAME = f"rc6-r163-offhost-{RUN}-{ATTEMPT}-{SHA}"
WORKFLOW = ".github/workflows/rc6-r163-offhost-guard79-proof.yml"
MANIFEST = "porota-artifact-byte-manifest-v1.json"
NATIVE_PATHS = (
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


def _json(data: object) -> bytes:
    return (json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _public_native_member(relative):
    raw_size = 67
    raw_digest = "0" * 64
    if relative.endswith(".xml"):
        return (
            '<testsuite schema="RC6_R206_PUBLIC_JUNIT_V1" '
            'status="PRIVATE_SOURCE_UNATTESTED" path="' + relative +
            '" name="R206_PUBLIC_SYNTHETIC_ONLY" tests="1" '
            'failures="0" errors="0" skipped="0" private_raw_size_bytes="' +
            str(raw_size) + '" private_raw_sha256_unattested="' +
            raw_digest + '" release_go="false">'
            '<testcase name="case-000001"/></testsuite>\n'
        ).encode("ascii")
    generations = 12 if relative.startswith("cadence/") else 2
    return _json({
        "schema": "RC6_R206_PUBLIC_JSON_V1",
        "status": "PRIVATE_SOURCE_UNATTESTED",
        "source_path": relative,
        "private_raw_size_bytes": raw_size,
        "private_raw_sha256_unattested": raw_digest,
        "synthetic_fixture_only": True,
        "release_go": False,
        "real_orders_sent": 0,
        "catalog_count": 14611,
        "ready_fixture_count": 9902,
        "required_generations": generations,
    })


def _tar(paths=NATIVE_PATHS, overrides=None) -> bytes:
    overrides = overrides or {}
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        for path in sorted(paths):
            data = overrides.get(path, _public_native_member(path))
            info = tarfile.TarInfo("./" + path)
            info.uid = info.gid = 1000
            info.mode = 0o600
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _native(tar_bytes):
    files = []
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as archive:
        for item in archive:
            path = item.name.removeprefix("./")
            data = archive.extractfile(item).read()
            files.append({"path": path, "size_bytes": len(data),
                          "sha256": hashlib.sha256(data).hexdigest()})
    files.sort(key=lambda row: row["path"])
    native = {
        "schema": "RC6_R163_NATIVE_TAR_V1", "schema_version": 1,
        "status": "OFFHOST_BYTES_ONLY", "candidate_sha": SHA,
        "candidate_tree_sha": TREE, "image_id": IMAGE_ID,
        "run_id": str(RUN), "run_attempt": str(ATTEMPT), "role": "bundle",
        "source_cid": SOURCE_CID, "collector_cid": COLLECTOR_CID,
        "volume_name": VOLUME, "tar_sha256": hashlib.sha256(tar_bytes).hexdigest(),
        "tar_size_bytes": len(tar_bytes), "file_count": len(files), "files": files,
        "host_or_runtime_verified": False,
    }
    return native


def _inspect(cid, *, collector):
    labels = {
        "porota.commit": SHA, "porota.candidate_tree": TREE,
        "porota.predeploy": "v2", "porota.native-proof": "v3",
        "porota.native-role": "bundle-collector" if collector else "bundle",
        "porota.run_id": str(RUN), "porota.run_attempt": str(ATTEMPT),
    }
    host = {
        "Privileged": False, "ReadonlyRootfs": True,
        "NetworkMode": "none", "CapDrop": ["ALL"], "CapAdd": None,
        "Devices": None, "Tmpfs": None, "PublishAllPorts": False,
        "PortBindings": None, "Links": None, "ExtraHosts": None,
        "SecurityOpt": ["no-new-privileges"], "Binds": None,
        "IpcMode": "private", "UTSMode": "", "PidMode": "",
        "UsernsMode": "", "CgroupnsMode": "private", "GroupAdd": None,
        "DeviceRequests": None, "DeviceCgroupRules": None, "Runtime": "runc",
        "Sysctls": None, "CgroupParent": "",
        "Mounts": [] if collector else [{"Type": "volume", "Source": "",
                                            "Target": "/app/data", "ReadOnly": False}],
        "VolumesFrom": [SOURCE_CID + ":ro"] if collector else None,
    }
    return {"Id": cid, "Image": IMAGE_ID,
            "Config": {"User": "1000:1000", "Labels": labels},
            "HostConfig": host,
            "Mounts": [{"Type": "volume", "Name": VOLUME,
                        "Source": "/var/lib/docker/volumes/anonymous/_data",
                        "Driver": "local", "Destination": "/app/data",
                        "Mode": "" if collector else "z", "RW": not collector}]}


def _public_inspect(cid, *, collector):
    """Synthetic V2 projection, never a replay of private Docker inspection."""
    raw = _inspect(cid, collector=collector)
    raw_bytes = _json([raw])
    host = dict(raw["HostConfig"])
    if not collector:
        host["Mounts"] = [{key: value for key, value in host["Mounts"][0].items()
                           if key != "Source"}]
    mount = {key: value for key, value in raw["Mounts"][0].items()
             if key != "Source"}
    return {
        "schema": "RC6_R163_PUBLIC_INSPECT_V2",
        "schema_version": 2,
        "role": "bundle-collector" if collector else "bundle",
        "status": "OFFHOST_PROJECTED_EVIDENCE_ONLY",
        "raw_inspect": {
            "sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "size_bytes": len(raw_bytes),
            "published": False,
        },
        "source_redaction": {
            "hostconfig_declaration": "NOT_APPLICABLE" if collector else "EMPTY",
            "runtime_source_nonempty_observed": True,
        },
        "record": {
            "Id": raw["Id"], "Image": raw["Image"],
            "Config": raw["Config"], "HostConfig": host, "Mounts": [mount],
        },
        "host_or_runtime_verified": False,
    }


def _evidence():
    tar_bytes = _tar()
    custody = {
        "schema_version": 1, "status": "OFFHOST_CONTRACT_ONLY",
        "candidate_sha": SHA, "candidate_tree_sha": TREE, "image_id": IMAGE_ID,
        "run_id": str(RUN), "run_attempt": str(ATTEMPT), "role": "bundle",
        "source_cid": SOURCE_CID, "collector_cid": COLLECTOR_CID,
        "volume_name": VOLUME, "mount_target": "/app/data",
        "uid": 1000, "gid": 1000, "host_or_runtime_verified": False,
    }
    native = _native(tar_bytes)
    entries = {
        "native.tar": tar_bytes, "native-manifest.json": _json(native),
        "custody.json": _json(custody),
        "source-inspect.json": _json(_public_inspect(SOURCE_CID, collector=False)),
        "collector-inspect.json": _json(_public_inspect(COLLECTOR_CID, collector=True)),
    }
    rows = [{"path": name, "type": "file", "size_bytes": len(data),
             "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in sorted(entries.items())]
    internal = {"schema_version": 1, "candidate_sha": SHA,
                "candidate_tree_sha": TREE, "run_id": RUN, "run_attempt": ATTEMPT,
                "artifact_name": NAME, "file_count": len(rows),
                "total_size_bytes": sum(row["size_bytes"] for row in rows), "files": rows}
    entries[MANIFEST] = _json(internal)
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    zip_bytes = payload.getvalue()
    digest = hashlib.sha256(zip_bytes).hexdigest()
    artifact = {"id": ARTIFACT_ID, "name": NAME, "digest": "sha256:" + digest,
                "size_in_bytes": len(zip_bytes), "expired": False,
                "workflow_run": {"id": RUN, "head_sha": SHA}}
    run = {"id": RUN, "run_attempt": ATTEMPT, "status": "completed",
           "conclusion": "success", "event": "pull_request",
           "name": "RC6 R163 Offhost Guard79 Proof (PROBE ONLY)",
           "path": WORKFLOW, "head_sha": SHA, "head_branch": HEAD_REF,
           "repository": {"full_name": REPO}, "head_repository": {"full_name": REPO},
           "pull_requests": [{"number": 1087, "head": {"sha": SHA},
                              "base": {"ref": BASE}}]}
    pr = {"number": 1087, "state": "open", "draft": True,
          "head": {"sha": SHA, "ref": HEAD_REF, "repo": {"full_name": REPO}},
          "base": {"ref": BASE, "repo": {"full_name": REPO}}}
    event = {"action": "completed", "repository": {"full_name": REPO},
             "workflow_run": {"id": RUN, "run_attempt": ATTEMPT,
                              "conclusion": "success", "status": "completed",
                              "head_sha": SHA, "event": "pull_request",
                              "name": run["name"], "path": WORKFLOW}}
    evidence = {"event": event, "run": run, "pr": pr,
                "commit": {"sha": SHA, "tree": {"sha": TREE}},
                "artifact_list": {"total_count": 1, "artifacts": [dict(artifact)]},
                "artifact": artifact, "zip_bytes": zip_bytes,
                "expected_run_id": RUN, "expected_run_attempt": ATTEMPT}
    evidence["attempt"] = dict(
        run, run_started_at="2026-11-12T03:04:13Z",
        created_at="2026-11-12T03:04:14Z",
        updated_at="2026-11-12T03:35:00Z",
    )
    for receipt in (evidence["artifact"], evidence["artifact_list"]["artifacts"][0]):
        receipt["created_at"] = "2026-11-12T03:30:00Z"
        receipt["updated_at"] = "2026-11-12T03:30:02Z"
    return evidence



def _evidence_with_attempt():
    return _evidence()


def _red(evidence, reason):
    try:
        verify_postrun_bytes(**evidence)
    except PostrunEvidenceRed as exc:
        assert str(exc) == reason, (str(exc), reason)
    else:
        raise AssertionError("EXPECTED_POSTRUN_RED:" + reason)


def _repack(evidence, *, rename=None, mutate=None, symlink=None):
    """Simulate a new immutable artifact with an internally consistent REST digest."""
    with zipfile.ZipFile(io.BytesIO(evidence["zip_bytes"])) as original:
        members = [(info.filename, original.read(info)) for info in original.infolist()]
    if mutate:
        members = [(name, mutate[name] if name in mutate else data)
                   for name, data in members]
    rewritten = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(rewritten, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in members:
                new_name = rename.get(name, name) if rename else name
                if new_name == symlink:
                    info = zipfile.ZipInfo(new_name)
                    info.create_system = 3
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    archive.writestr(info, data)
                else:
                    archive.writestr(new_name, data)
    evidence["zip_bytes"] = rewritten.getvalue()
    digest = "sha256:" + hashlib.sha256(evidence["zip_bytes"]).hexdigest()
    for receipt in (evidence["artifact"], evidence["artifact_list"]["artifacts"][0]):
        receipt["digest"] = digest
        receipt["size_in_bytes"] = len(evidence["zip_bytes"])



def _reseal_zip_only(evidence, zip_bytes):
    """Attacker reseals both REST receipts, with inner file manifest unchanged."""
    evidence["zip_bytes"] = bytes(zip_bytes)
    digest = "sha256:" + hashlib.sha256(evidence["zip_bytes"]).hexdigest()
    for receipt in (evidence["artifact"], evidence["artifact_list"]["artifacts"][0]):
        receipt["digest"] = digest
        receipt["size_in_bytes"] = len(evidence["zip_bytes"])


def _assert_readable_zip(zip_bytes):
    """Canaries should pass the standard Python parser before the strict guard."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        assert len(archive.infolist()) == 6
        for info in archive.infolist():
            assert archive.read(info)


def _zip_with_metadata(evidence, *, archive_comment=b"", member_comment=b"",
                       member_extra=b""):
    original = io.BytesIO(evidence["zip_bytes"])
    rewritten = io.BytesIO()
    with zipfile.ZipFile(original, "r") as source:
        with zipfile.ZipFile(rewritten, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for info in source.infolist():
                entry = zipfile.ZipInfo(info.filename)
                entry.compress_type = zipfile.ZIP_DEFLATED
                if info.filename == "custody.json":
                    entry.comment = member_comment
                    entry.extra = member_extra
                target.writestr(entry, source.read(info))
            target.comment = archive_comment
    _reseal_zip_only(evidence, rewritten.getvalue())
    _assert_readable_zip(evidence["zip_bytes"])


def _inject_zip32_bytes(raw, at, hidden, *, local_extra=False,
                        stream_trailing=False):
    """Byte-mutate ZIP32, adjusting central offsets so stock zipfile still reads it."""
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
    eocd = len(raw) - 22
    assert raw[eocd:eocd + 4] == b"PK\x05\x06"
    cd_offset = struct.unpack_from("<I", raw, eocd + 16)[0]
    cd_size = struct.unpack_from("<I", raw, eocd + 12)[0]
    assert at <= cd_offset and not (local_extra and stream_trailing)
    rewritten = bytearray(raw)
    rewritten[at:at] = hidden
    shift = len(hidden)
    cursor = cd_offset + shift
    for info in infos:
        assert rewritten[cursor:cursor + 4] == b"PK\x01\x02"
        name_len, extra_len, comment_len = struct.unpack_from("<HHH", rewritten, cursor + 28)
        old_local = info.header_offset
        if old_local >= at:
            struct.pack_into("<I", rewritten, cursor + 42, old_local + shift)
        if stream_trailing and info.filename == infos[0].filename:
            struct.pack_into("<I", rewritten, cursor + 20, info.compress_size + shift)
        cursor += 46 + name_len + extra_len + comment_len
    assert cursor == cd_offset + shift + cd_size
    struct.pack_into("<I", rewritten, eocd + shift + 16, cd_offset + shift)
    if local_extra:
        struct.pack_into("<H", rewritten, infos[0].header_offset + 28, shift)
    if stream_trailing:
        struct.pack_into("<I", rewritten, infos[0].header_offset + 18,
                         infos[0].compress_size + shift)
    return bytes(rewritten)



def _central_only_zip32_extra(raw, extra):
    """Valid ZIP32 central-only extra; payload and local headers unchanged."""
    eocd = len(raw) - 22
    cd_at = struct.unpack_from("<I", raw, eocd + 16)[0]
    cd_size = struct.unpack_from("<I", raw, eocd + 12)[0]
    assert raw[cd_at:cd_at + 4] == b"PK\x01\x02"
    name_length, extra_length = struct.unpack_from("<HH", raw, cd_at + 28)
    assert extra_length == 0
    at = cd_at + 46 + name_length
    out = bytearray(raw)
    out[at:at] = extra
    struct.pack_into("<H", out, cd_at + 30, len(extra))
    struct.pack_into("<I", out, eocd + len(extra) + 12, cd_size + len(extra))
    return bytes(out)


class _NonSeekableZipSink(io.BytesIO):
    def seekable(self):
        return False

    def seek(self, *_args):
        raise io.UnsupportedOperation("force signed streaming data descriptors")



def _stored_zip(evidence):
    target = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(evidence["zip_bytes"])) as source:
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as output:
            for info in source.infolist():
                output.writestr(info.filename, source.read(info))
    _reseal_zip_only(evidence, target.getvalue())
    _assert_readable_zip(evidence["zip_bytes"])
    with zipfile.ZipFile(io.BytesIO(evidence["zip_bytes"])) as archive:
        assert all(item.compress_type == zipfile.ZIP_STORED and
                   item.compress_size == item.file_size
                   for item in archive.infolist())


def _streaming_descriptor_zip(evidence):
    target = _NonSeekableZipSink()
    with zipfile.ZipFile(io.BytesIO(evidence["zip_bytes"])) as source:
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for info in source.infolist():
                output.writestr(info.filename, source.read(info))
    _reseal_zip_only(evidence, target.getvalue())
    _assert_readable_zip(evidence["zip_bytes"])
    with zipfile.ZipFile(io.BytesIO(evidence["zip_bytes"])) as archive:
        assert all((item.flag_bits & 8) == 8 for item in archive.infolist())


def _first_zip32_file_parts(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        first = sorted(archive.infolist(), key=lambda item: item.header_offset)[0]
        second = sorted(archive.infolist(), key=lambda item: item.header_offset)[1]
    local = first.header_offset
    assert raw[local:local + 4] == b"PK\x03\x04"
    name_len, extra_len = struct.unpack_from("<HH", raw, local + 26)
    compressed_begin = local + 30 + name_len + extra_len
    return first, compressed_begin, compressed_begin + first.compress_size, second.header_offset


def _reseal_native_tar(evidence, raw_tar):
    with zipfile.ZipFile(io.BytesIO(evidence["zip_bytes"])) as original:
        payload = {name: original.read(name) for name in original.namelist()}
    native = json.loads(payload["native-manifest.json"])
    native["tar_sha256"] = hashlib.sha256(raw_tar).hexdigest()
    native["tar_size_bytes"] = len(raw_tar)
    native["files"] = _native(raw_tar)["files"]
    native["file_count"] = len(native["files"])
    payload["native.tar"] = raw_tar
    payload["native-manifest.json"] = _json(native)
    manifest = json.loads(payload[MANIFEST])
    for row in manifest["files"]:
        data = payload[row["path"]]
        row["size_bytes"] = len(data)
        row["sha256"] = hashlib.sha256(data).hexdigest()
    manifest["total_size_bytes"] = sum(row["size_bytes"] for row in manifest["files"])
    payload[MANIFEST] = _json(manifest)
    _repack(evidence, mutate=payload)


def _reseal_inspect(evidence, file_name, mutator=None, *, envelope=False,
                    replacement=None):
    with zipfile.ZipFile(io.BytesIO(evidence["zip_bytes"])) as original:
        payload = {name: original.read(name) for name in original.namelist()}
    inspect = json.loads(payload[file_name])
    if replacement is not None:
        inspect = replacement
    else:
        assert mutator is not None
        mutator(inspect if envelope else inspect["record"])
    payload[file_name] = _json(inspect)
    outer = json.loads(payload[MANIFEST])
    for row in outer["files"]:
        data = payload[row["path"]]
        row["size_bytes"] = len(data)
        row["sha256"] = hashlib.sha256(data).hexdigest()
    outer["total_size_bytes"] = sum(row["size_bytes"] for row in outer["files"])
    payload[MANIFEST] = _json(outer)
    _repack(evidence, mutate=payload)


def test_trusted_bytes_attest_exact_run_artifact_tree_and_tar_without_release_go():
    result = verify_postrun_bytes(**_evidence())
    assert result["status"] == "POSTRUN_ARTIFACT_BYTES_MATCH"
    assert result["run_id"] == RUN and result["run_attempt"] == ATTEMPT
    assert result["artifact_id"] == ARTIFACT_ID
    assert result["candidate_sha"] == SHA and result["candidate_tree_sha"] == TREE
    assert result["native_file_count"] == 13
    assert result["automatic_go"] is False
    assert result["release_go"] is False
    assert result["runtime_verified"] is False
    assert result["raw_inspect_contents_verified_by_this_guard"] is False


def test_r206_trusted_main_independently_rejects_opaque_native_members():
    """A coherent, fully resealed ZIP must not hide private fields in TAR13."""
    for name in NATIVE_PATHS:
        evidence = _evidence()
        forged = _tar(overrides={
            name: _public_native_member(name) + b"RC6_OPAQUE_PRIVATE_CANARY"
        })
        _reseal_native_tar(evidence, forged)
        _red(evidence, "PUBLIC_NATIVE_SCHEMA_INVALID")


def test_r206_trusted_main_rejects_forged_zero_orders_and_release_in_json():
    name = "session/stress.json"
    for key, value in (("real_orders_sent", 1), ("release_go", True)):
        evidence = _evidence()
        forged = json.loads(_public_native_member(name))
        forged[key] = value
        _reseal_native_tar(evidence, _tar(overrides={name: _json(forged)}))
        _red(evidence, "PUBLIC_NATIVE_SCHEMA_INVALID")


def _r206_publisher_receipts():
    upstream = _evidence()
    for record in (upstream["event"]["workflow_run"], upstream["run"],
                   upstream["attempt"]):
        record["conclusion"] = "failure"
    publisher = {
        "id": 2002, "run_attempt": 3, "status": "completed",
        "conclusion": "success", "event": "workflow_run",
        "name": "RC6 R206 Trusted Main Preupload Publisher (PROBE ONLY)",
        "path": ".github/workflows/rc6-r163-postrun-trusted-main.yml",
        "head_sha": "c" * 40, "head_branch": "main",
        "repository": {"full_name": REPO},
        "head_repository": {"full_name": REPO},
    }
    attempt = dict(
        publisher, run_started_at="2026-11-12T08:00:00Z",
        created_at="2026-11-12T08:00:01Z",
        updated_at="2026-11-12T08:05:00Z",
    )
    return upstream, publisher, attempt


def test_r206_trusted_main_binds_distinct_upstream_and_publisher_attempts():
    upstream, publisher, attempt = _r206_publisher_receipts()
    identity = trusted.verify_trusted_publisher_identity(
        upstream_event=upstream["event"], upstream_run=upstream["run"],
        upstream_attempt=upstream["attempt"], pr=upstream["pr"],
        commit=upstream["commit"], publisher_run=publisher,
        publisher_attempt=attempt, expected_upstream_run_id=RUN,
        expected_upstream_attempt=ATTEMPT, expected_publisher_run_id=2002,
        expected_publisher_attempt=3,
    )
    assert identity["candidate_sha"] == SHA
    assert identity["candidate_tree_sha"] == TREE
    assert identity["upstream_run_id"] == RUN
    assert identity["upstream_attempt"] == ATTEMPT
    assert identity["publisher_main_sha"] == "c" * 40
    assert identity["publisher_run_id"] == 2002
    assert identity["publisher_attempt"] == 3
    assert identity["release_go"] is False
    assert identity["upstream_ci_green_verified"] is False


def test_r206_trusted_main_rejects_prior_attempt_as_current_publisher():
    upstream, publisher, attempt = _r206_publisher_receipts()
    attempt["run_attempt"] = 2
    with __import__("pytest").raises(
        PostrunEvidenceRed, match="^PUBLISHER_ATTEMPT_MISMATCH$"
    ):
        trusted.verify_trusted_publisher_identity(
            upstream_event=upstream["event"], upstream_run=upstream["run"],
            upstream_attempt=upstream["attempt"], pr=upstream["pr"],
            commit=upstream["commit"], publisher_run=publisher,
            publisher_attempt=attempt, expected_upstream_run_id=RUN,
            expected_upstream_attempt=ATTEMPT, expected_publisher_run_id=2002,
            expected_publisher_attempt=3,
        )


def test_r206_preupload_refuses_reachable_same_uid_writer():
    with tempfile.TemporaryDirectory(prefix="rc6-r206-main-guard-") as root:
        for false_claim in (False, True):
            with __import__("pytest").raises(
                PostrunEvidenceRed, match="^PREUPLOAD_SAME_UID_WRITER$"
            ):
                trusted.assert_trusted_preupload_writer_isolation(
                    publisher_uid=os.getuid(), candidate_writer_uid=os.getuid(),
                    publisher_staging_root=Path(root),
                    candidate_can_access_staging=false_claim,
                )


def test_r206_trusted_preupload_seals_public_five_members_without_raw_inspect():
    with tempfile.TemporaryDirectory(prefix="rc6-r206-main-seal-") as root:
        members = trusted.build_trusted_preupload_members(
            producer_private_inspect=_json([_inspect(SOURCE_CID, collector=False)]),
            collector_private_inspect=_json([_inspect(COLLECTOR_CID, collector=True)]),
            native_tar=_tar(), candidate_sha=SHA, candidate_tree_sha=TREE,
            run_id=RUN, run_attempt=ATTEMPT, image_id=IMAGE_ID,
            source_cid=SOURCE_CID, collector_cid=COLLECTOR_CID,
            volume_name=VOLUME, publisher_staging_root=Path(root),
            publisher_uid=os.getuid(), candidate_writer_uid=os.getuid() + 1,
            candidate_can_access_staging=False,
        )
    assert set(members) == {
        "source-inspect.json", "collector-inspect.json", "custody.json",
        "native.tar", "native-manifest.json",
    }
    assert members["native.tar"] == _tar()
    assert all(b"/var/lib/docker/" not in value for value in members.values())
    assert all(b"Config.Env" not in value for value in members.values())
    assert json.loads(members["source-inspect.json"])["raw_inspect"]["published"] is False


def test_r206_private_source_declaration_cannot_hide_nonempty_named_source():
    private = _inspect(SOURCE_CID, collector=False)
    private["HostConfig"]["Mounts"][0]["Source"] = "attacker-named-volume"
    with tempfile.TemporaryDirectory(prefix="rc6-r206-source-red-") as root:
        with __import__("pytest").raises(
            PostrunEvidenceRed, match="^PREUPLOAD_PRIVATE_MOUNT_INVALID$"
        ):
            trusted.build_trusted_preupload_members(
                producer_private_inspect=_json([private]),
                collector_private_inspect=_json([_inspect(COLLECTOR_CID, collector=True)]),
                native_tar=_tar(), candidate_sha=SHA, candidate_tree_sha=TREE,
                run_id=RUN, run_attempt=ATTEMPT, image_id=IMAGE_ID,
                source_cid=SOURCE_CID, collector_cid=COLLECTOR_CID,
                volume_name=VOLUME, publisher_staging_root=Path(root),
                publisher_uid=os.getuid(), candidate_writer_uid=os.getuid() + 1,
                candidate_can_access_staging=False,
            )


def test_r206_trusted_preupload_writes_six_privacy_verified_files_in_private_stage():
    with tempfile.TemporaryDirectory(prefix="rc6-r206-main-writer-") as root:
        staging = Path(root)
        input_root = staging / "private"
        input_root.mkdir(mode=0o700)
        (input_root / "source-inspect.json").write_bytes(
            _json([_inspect(SOURCE_CID, collector=False)]))
        (input_root / "collector-inspect.json").write_bytes(
            _json([_inspect(COLLECTOR_CID, collector=True)]))
        (input_root / "native.tar").write_bytes(_tar())
        for path in input_root.iterdir():
            path.chmod(0o600)
        output = trusted.seal_trusted_preupload_directory(
            publisher_staging_root=staging, input_root=input_root,
            output_name="public", candidate_sha=SHA, candidate_tree_sha=TREE,
            run_id=RUN, run_attempt=ATTEMPT, image_id=IMAGE_ID,
            source_cid=SOURCE_CID, collector_cid=COLLECTOR_CID,
            volume_name=VOLUME, publisher_uid=os.getuid(),
            candidate_writer_uid=os.getuid() + 1,
            candidate_can_access_staging=False,
        )
        files = {path.name: path.read_bytes() for path in output.iterdir()}
        assert set(files) == set(trusted.ZIP_PAYLOAD) | {MANIFEST}
        assert output.stat().st_mode & 0o777 == 0o700
        assert all(path.stat().st_mode & 0o777 == 0o600 for path in output.iterdir())
        assert all(b"/var/lib/docker/" not in data for data in files.values())
        assert json.loads(files[MANIFEST])["run_id"] == RUN
        assert json.loads(files[MANIFEST])["run_attempt"] == ATTEMPT


def test_r206_trusted_publisher_rechecks_upstream_failure_and_zero_artifacts():
    client = _R265FakeGitHub()
    result = client.check()
    assert result["candidate_sha"] == SHA
    assert result["candidate_tree_sha"] == TREE
    assert result["upstream_ci_green_verified"] is False
    client.rows[client.run_path + "/artifacts?per_page=100"] = {
        "total_count": 1, "artifacts": [{"id": 88}],
    }
    with __import__("pytest").raises(
        PostrunEvidenceRed, match="^PREUPLOAD_UPSTREAM_ARTIFACT_PRESENT$"
    ):
        client.check()


def _r265_upstream_step_receipts():
    """Synthetic GitHub *attempt-specific* job metadata; no raw JUnit/logs."""
    py_names = (
        "Reject non-GitHub-hosted runner (HOLD79)",
        "Exact SHA checkout without persisted credentials",
        "Python 3.11",
        "Locked dependency closure and import smoke",
        "SRE health PAPER-zero regression (in-memory SQLite only)",
        "A976 A785 R164 R203 offline regression matrix (no host)",
        "R206 XML and JSON opaque native projection regression (offhost TDD)",
        "R205 signals risk exit ledger exact 191 fixture diagnostic (offline)",
        "PPI quota and capability fake-wire regressions on Python 3.11",
        "Replay failed host transition regression fixtures",
        "Verify mount identity and redacted HOLD79 diagnostics on Python 3.11",
    )
    native_names = (
        "Reject non-GitHub-hosted runner (HOLD79)",
        "Checkout exact candidate without persisted credentials",
        "Strict candidate identity",
        "Setup isolated image builder",
        "Build one ephemeral PROBE image, never push",
        "Run native stages in one UID1000 anonymous volume",
        "R205 raw JUnit privacy hold before any upload (R206 required)",
        "Seal all evidence before upload (artifact ID assigned later)",
        "Upload sealed offhost probe evidence (never release artifact)",
    )

    def step(name, number, conclusion="success"):
        return {"name": name, "number": number, "status": "completed",
                "conclusion": conclusion}

    common = {"run_id": RUN, "head_sha": SHA, "status": "completed"}
    return {"total_count": 3, "jobs": [
        dict(common, id=4101, name="Python 3.11 lock and incident fixtures (offhost)",
             conclusion="success", steps=(
                 [step("Set up job", 1)] +
                 [step(name, n) for n, name in enumerate(py_names, 2)] +
                 [step("Post Python 3.11", 23),
                  step("Post Exact SHA checkout without persisted credentials", 24),
                  step("Complete job", 25)])),
        dict(common, id=4102, name="Native CID and anonymous volume producer (offhost)",
             conclusion="failure", steps=(
                 [step("Set up job", 1)] +
                 [step(name, n, "failure" if n == 8 else
                       "skipped" if n in (9, 10) else "success")
                  for n, name in enumerate(native_names, 2)] +
                 [step("Post Build one ephemeral PROBE image, never push", 18),
                  step("Post Setup isolated image builder", 19),
                  step("Post Checkout exact candidate without persisted credentials", 20),
                  step("Complete job", 21)])),
        dict(common, id=4103, name="Separate-runner preliminary raw REST bytes (NO RELEASE)",
             conclusion="skipped", steps=None),
    ]}


def _r265_source_receipt():
    """A small synthetic fixture with hashes injected only into offline tests."""
    python = (
        "  python311-lock-closure:\n"
        "    name: Python 3.11 lock and incident fixtures (offhost)\n"
        "    steps:\n"
        "      - name: Locked dependency closure and import smoke\n"
        "        run: python -m pip check\n"
    )
    native = (
        "  offhost-native-proof:\n"
        "    name: Native CID and anonymous volume producer (offhost)\n"
        "    needs: [python311-lock-closure]\n"
        "    steps:\n"
        "      - name: Run native stages in one UID1000 anonymous volume\n"
        "        run: true\n"
        "      - name: R205 raw JUnit privacy hold before any upload (R206 required)\n"
        "        run: |\n"
        "          echo RC6_R205_JUNIT_PRIVACY=HOLD79\n"
        "          exit 79\n"
    )
    source = ("name: RC6 R163 Offhost Guard79 Proof (PROBE ONLY)\n"
              "jobs:\n" + python + native +
              "  offhost-independent-bytes:\n    needs: [offhost-native-proof]\n")
    raw = source.encode()
    receipt = {
        "path": WORKFLOW, "name": WORKFLOW.rsplit("/", 1)[-1],
        "encoding": "base64", "content": base64.encodebytes(raw).decode(),
        "size": len(raw),
        "sha": hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest(),
    }
    return receipt, (hashlib.sha256(python.encode()).hexdigest(),
                     hashlib.sha256(native.encode()).hexdigest())


class _R265FakeGitHub:
    def __init__(self):
        upstream, _, _ = _r206_publisher_receipts()
        prefix = f"/repos/{REPO}"
        self.run_path = f"{prefix}/actions/runs/{RUN}"
        self.jobs_path = self.run_path + f"/attempts/{ATTEMPT}/jobs?per_page=100"
        self.source_path = f"{prefix}/contents/{WORKFLOW}?ref={SHA}"
        self.source, self.source_hashes = _r265_source_receipt()
        self.reviewed_source_hash = hashlib.sha256(
            base64.b64decode(self.source["content"])).hexdigest()
        self.rows = {
            self.run_path: upstream["run"],
            self.run_path + f"/attempts/{ATTEMPT}": upstream["attempt"],
            f"{prefix}/pulls/1087": upstream["pr"],
            f"{prefix}/git/commits/{SHA}": upstream["commit"],
            self.run_path + "/artifacts?per_page=100":
                {"total_count": 0, "artifacts": []},
            self.jobs_path: _r265_upstream_step_receipts(),
            self.source_path: self.source,
        }
        self.event = upstream["event"]
        self.calls = []

    def get_json(self, path):
        self.calls.append(path)
        return copy.deepcopy(self.rows[path])

    def check(self):
        with (mock.patch.object(trusted, "UPSTREAM_PYTHON_JOB_SHA256",
                                self.source_hashes[0], create=True),
              mock.patch.object(trusted, "UPSTREAM_NATIVE_JOB_SHA256",
                                self.source_hashes[1], create=True),
              mock.patch.object(trusted, "UPSTREAM_REVIEWED_SOURCE_SHA256",
                                frozenset({self.reviewed_source_hash}), create=True)):
            return trusted.verify_upstream_preupload_from_github(
                self, event=self.event, expected_run_id=RUN,
                expected_run_attempt=ATTEMPT)


def test_r265_genuine_r205_failure_requires_trusted_attempt_jobs_before_b_build():
    client = _R265FakeGitHub()
    result = client.check()
    assert result["status"] == "UPSTREAM_R205_STAGE_MATCH_REPRODUCE_REQUIRED"
    assert result["upstream_ci_green_verified"] is False
    assert result["r205_exit_code_verified"] is False
    assert result["release_go"] is False
    assert client.jobs_path in client.calls
    assert client.source_path in client.calls
    assert "/jobs?per_page=100" not in client.calls


def test_r265_early_python_failure_holds_without_redundant_b_build():
    client = _R265FakeGitHub()
    jobs = client.rows[client.jobs_path]["jobs"]
    jobs[0]["conclusion"] = "failure"
    jobs[0]["steps"][5]["conclusion"] = "failure"
    jobs[1]["conclusion"] = "skipped"
    jobs[1]["steps"] = None
    with __import__("pytest").raises(
        PostrunEvidenceRed, match="^PREUPLOAD_PYTHON_JOB_NOT_SUCCESS$"
    ):
        client.check()


def test_r265_cancelled_a_is_not_intentional_r205_hold():
    client = _R265FakeGitHub()
    for receipt in (client.event["workflow_run"], client.rows[client.run_path],
                    client.rows[client.run_path + f"/attempts/{ATTEMPT}"]):
        receipt["conclusion"] = "cancelled"
    with __import__("pytest").raises(
        PostrunEvidenceRed, match="^EVENT_UPSTREAM_NOT_SUCCESS$"
    ):
        client.check()


def test_r265_missing_ambiguous_or_foreign_native_job_is_hold():
    for mutation in ("missing", "duplicate", "different_sha", "unknown_job"):
        client = _R265FakeGitHub()
        jobs = client.rows[client.jobs_path]
        if mutation == "missing":
            jobs["jobs"].pop(1)
        elif mutation == "duplicate":
            jobs["jobs"].append(copy.deepcopy(jobs["jobs"][1]))
            jobs["jobs"][-1]["id"] = 4114
        elif mutation == "different_sha":
            jobs["jobs"][1]["head_sha"] = "d" * 40
        else:
            jobs["jobs"][1]["name"] = "Different native job"
        jobs["total_count"] = len(jobs["jobs"])
        with __import__("pytest").raises(
            PostrunEvidenceRed, match="^PREUPLOAD_JOB_IDENTITY_MISMATCH$"
        ):
            client.check()


def test_r265_missing_skipped_or_wrong_r205_stage_is_hold():
    for mutation in ("missing_native", "skipped_native", "wrong_stage", "hold_success"):
        client = _R265FakeGitHub()
        steps = client.rows[client.jobs_path]["jobs"][1]["steps"]
        if mutation == "missing_native":
            steps.pop(6)
        elif mutation == "skipped_native":
            steps[6]["conclusion"] = "skipped"
        elif mutation == "wrong_stage":
            steps[7]["name"] = "Renamed R205 step"
        else:
            steps[7]["conclusion"] = "success"
        with __import__("pytest").raises(
            PostrunEvidenceRed, match="^PREUPLOAD_NATIVE_STAGE_MISMATCH$"
        ):
            client.check()


def test_r265_upstream_source_forged_hold_script_rejected_even_if_steps_match():
    client = _R265FakeGitHub()
    raw = base64.b64decode(client.source["content"])
    forged = raw.replace(b"exit 79", b"exit 13")
    client.source["content"] = base64.encodebytes(forged).decode()
    client.source["sha"] = hashlib.sha1(
        f"blob {len(forged)}\0".encode() + forged).hexdigest()
    client.rows[client.source_path] = client.source
    with __import__("pytest").raises(
        PostrunEvidenceRed, match="^PREUPLOAD_WORKFLOW_SOURCE_MISMATCH$"
    ):
        client.check()


def test_r265_source_rejects_duplicate_jobs_root_or_extra_unreviewed_job():
    for added in (
        b"\njobs:\n  forged-jobs:\n    name: unexpected\n",
        b"\n  forged-jobs:\n    name: unexpected\n",
    ):
        client = _R265FakeGitHub()
        raw = base64.b64decode(client.source["content"]) + added
        client.source["content"] = base64.encodebytes(raw).decode()
        client.source["size"] = len(raw)
        client.source["sha"] = hashlib.sha1(
            f"blob {len(raw)}\0".encode() + raw).hexdigest()
        client.rows[client.source_path] = client.source
        with __import__("pytest").raises(
            PostrunEvidenceRed, match="^PREUPLOAD_WORKFLOW_SOURCE_MISMATCH$"
        ):
            client.check()


def test_r265_pr_top_level_shell_env_injection_rejected_despite_same_job_hashes():
    """SEC: BASH_ENV can change every bash step without editing reviewed jobs."""
    for injection in (
        b"env:\n  BASH_ENV: .github/ci-workflow-shell-startup.sh\n",
        b"defaults:\n  run:\n    shell: bash -e {0}\n",
    ):
        client = _R265FakeGitHub()
        original = base64.b64decode(client.source["content"])
        forged = original.replace(b"jobs:\n", injection + b"jobs:\n", 1)
        assert forged != original
        client.source["content"] = base64.encodebytes(forged).decode()
        client.source["size"] = len(forged)
        client.source["sha"] = hashlib.sha1(
            f"blob {len(forged)}\0".encode() + forged).hexdigest()
        client.rows[client.source_path] = client.source
        with __import__("pytest").raises(
            PostrunEvidenceRed, match="^PREUPLOAD_WORKFLOW_SOURCE_MISMATCH$"
        ):
            client.check()


def test_r265_final_a_without_ready_trigger_is_reviewed_source():
    # Verified Git SHA 8b1b51b; no ready_for_review redundant A build.
    assert "5c068762f68d019851792ba61e3b5e6be387441f67607ba11228efba87361afa" in (
        trusted.UPSTREAM_REVIEWED_SOURCE_SHA256)


def test_r268_cache_probe_source_requires_exact_reviewed_job_pair():
    # The approved cache-only variant has a different full YAML and different
    # Python/native job bytes.  A full-source pin alone must not admit a job
    # from a different variant, even when the old R265 pair remains trusted.
    cache_source = "22cf40da38929a0f5976b153935ef52b55a99164c568cf9ecf36c0af254e4135"
    cache_jobs = (
        "d5f2dab236acc5047b5aee676209a884a8265a55450c00621ae68ef5bf4944d6",
        "9c4bf8596ce57837cc075b89dc06ad759b1cabd219fdf00eb2672d334f3e2e18",
    )
    assert cache_source in trusted.UPSTREAM_REVIEWED_SOURCE_SHA256
    assert trusted.UPSTREAM_REVIEWED_CACHE_JOB_SHA256 == {cache_source: cache_jobs}

    client = _R265FakeGitHub()
    # The synthetic byte fixture acts as another reviewed variant only inside
    # this mock: a wrong per-variant Python hash must still HOLD79.
    with mock.patch.object(
        trusted, "UPSTREAM_REVIEWED_CACHE_JOB_SHA256",
        {client.reviewed_source_hash: ("0" * 64, client.source_hashes[1])},
    ):
        with __import__("pytest").raises(
            PostrunEvidenceRed, match="^PREUPLOAD_WORKFLOW_SOURCE_MISMATCH$"
        ):
            client.check()


def test_r265_intermediate_speed_variant_is_not_reviewed_source():
    # Neither f266317 nor bed8082 is the final (8b1b51b) A source.
    for superseded in (
        "14bb5cf5367ff1b33fd4f96d8603efa8cd511dbab98a898a8b2266c67c8fb1c2",
        "6a29148fff4714c4fb0115f2a3d32d2104559ab4a4eb323525b7ac6e23825f23",
    ):
        assert superseded not in trusted.UPSTREAM_REVIEWED_SOURCE_SHA256
    assert len(trusted.UPSTREAM_REVIEWED_SOURCE_SHA256) == 3


def test_r265_malformed_unhashable_jobs_name_holds_as_bounded_reason():
    client = _R265FakeGitHub()
    client.rows[client.jobs_path]["jobs"][0]["name"] = ["forged"]
    with __import__("pytest").raises(
        PostrunEvidenceRed, match="^PREUPLOAD_JOB_IDENTITY_MISMATCH$"
    ):
        client.check()


def test_r265_upstream_jobs_race_and_pr_head_staleness_fail_closed():
    client = _R265FakeGitHub()
    client.rows[f"/repos/{REPO}/pulls/1087"]["head"]["sha"] = "c" * 40
    with __import__("pytest").raises(PostrunEvidenceRed, match="^PR_HEAD_STALE$"):
        client.check()

    client = _R265FakeGitHub()
    original_get = client.get_json

    def changed_after_validation(path):
        row = original_get(path)
        if path == client.jobs_path and client.calls.count(path) == 2:
            row["jobs"][1]["conclusion"] = "cancelled"
        return row

    client.get_json = changed_after_validation
    with __import__("pytest").raises(
        PostrunEvidenceRed, match="^PREUPLOAD_UPSTREAM_REST_RACE$"
    ):
        client.check()


def test_r206_workflow_path_accepts_only_exact_github_at_ref_forms():
    for allowed in (WORKFLOW, WORKFLOW + "@" + HEAD_REF,
                    WORKFLOW + "@refs/heads/" + HEAD_REF,
                    WORKFLOW + "@refs/pull/1087/merge", WORKFLOW + "@" + SHA):
        evidence = _evidence()
        evidence["event"]["workflow_run"]["path"] = allowed
        evidence["run"]["path"] = allowed
        evidence["attempt"]["path"] = allowed
        assert verify_postrun_bytes(**evidence)["candidate_sha"] == SHA
    for bad in (WORKFLOW + "@main", WORKFLOW + "@refs/pull/999/merge",
                WORKFLOW + "@" + "f" * 40, WORKFLOW + "@HEAD"):
        evidence = _evidence()
        evidence["event"]["workflow_run"]["path"] = bad
        evidence["run"]["path"] = bad
        evidence["attempt"]["path"] = bad
        _red(evidence, "RUN_WORKFLOW_MISMATCH")


def _r206_c_postcompletion_client():
    """Separate P/N publisher artifact; U/M failed and has zero artifacts."""
    evidence, publisher, publisher_attempt = _r206_publisher_receipts()
    package = copy.deepcopy(evidence["artifact"])
    package["workflow_run"] = {"id": 2002, "head_sha": "c" * 40}
    package["created_at"] = "2026-11-12T08:03:00Z"
    package["updated_at"] = "2026-11-12T08:03:01Z"
    prefix = f"/repos/{REPO}"
    upstream_path = f"{prefix}/actions/runs/{RUN}"
    publisher_path = f"{prefix}/actions/runs/2002"
    routes = {
        upstream_path: evidence["run"],
        upstream_path + f"/attempts/{ATTEMPT}": evidence["attempt"],
        upstream_path + "/artifacts?per_page=100": {"total_count": 0, "artifacts": []},
        f"{prefix}/pulls/1087": evidence["pr"],
        f"{prefix}/git/commits/{SHA}": evidence["commit"],
        publisher_path: publisher,
        publisher_path + "/attempts/3": publisher_attempt,
        publisher_path + "/artifacts?per_page=100": {
            "total_count": 1, "artifacts": [copy.deepcopy(package)]},
        f"{prefix}/actions/artifacts/{ARTIFACT_ID}": package,
    }

    class Client:
        def __init__(self):
            self.after_download = False
            self.mutate = None

        def get_json(self, path):
            if self.after_download and self.mutate is not None:
                self.mutate(routes)
                self.mutate = None
            return copy.deepcopy(routes[path])

        def get_zip(self, artifact_id):
            assert artifact_id == ARTIFACT_ID
            self.after_download = True
            return evidence["zip_bytes"]

    event = {"action": "completed", "repository": {"full_name": REPO},
             "workflow_run": copy.deepcopy(publisher)}
    return Client(), event, routes


def test_r206_c_audits_completed_publisher_bytes_not_failed_upstream_artifacts():
    client, event, _ = _r206_c_postcompletion_client()
    outcome = trusted.audit_publisher_from_github(
        client, event=event, expected_publisher_run_id=2002,
        expected_publisher_attempt=3, trusted_main_sha="c" * 40)
    assert outcome["status"] == "POSTCOMPLETION_PUBLIC_PROBE_BYTES_MATCH"
    assert outcome["upstream_run_id"] == RUN
    assert outcome["publisher_run_id"] == 2002
    assert outcome["publisher_attempt"] == 3
    assert outcome["native_file_count"] == 13
    assert outcome["publisher_to_upstream_event_link_verified"] is False
    assert outcome["upstream_ci_green_verified"] is False
    assert outcome["release_go"] is False and outcome["runtime_verified"] is False


def test_r206_c_rejects_wrong_publisher_receipts_or_upstream_replay():
    for mutator, reason in (
        (lambda e, r: e["workflow_run"].__setitem__("run_attempt", 2),
         "POSTCOMPLETION_EVENT_MISMATCH"),
        (lambda e, r: r[f"/repos/{REPO}/actions/runs/2002"].__setitem__(
            "conclusion", "failure"), "PUBLISHER_IDENTITY_MISMATCH"),
        (lambda e, r: r[f"/repos/{REPO}/actions/runs/2002"].__setitem__(
            "head_sha", "d" * 40), "POSTCOMPLETION_MAIN_SHA_MISMATCH"),
        (lambda e, r: r[f"/repos/{REPO}/actions/artifacts/{ARTIFACT_ID}"][
            "workflow_run"].__setitem__("id", RUN), "ARTIFACT_RUN_MISMATCH"),
        (lambda e, r: r[f"/repos/{REPO}/actions/runs/{RUN}/artifacts?per_page=100"
                        ].update(total_count=1, artifacts=[{"id": 777}]),
         "PREUPLOAD_UPSTREAM_ARTIFACT_PRESENT"),
        (lambda e, r: r[f"/repos/{REPO}/actions/runs/2002/artifacts?per_page=100"
                        ].update(total_count=2, artifacts=[{"id": 883}, {"id": 884}]),
         "POSTCOMPLETION_ARTIFACT_AMBIGUOUS"),
    ):
        client, event, routes = _r206_c_postcompletion_client()
        mutator(event, routes)
        with __import__("pytest").raises(PostrunEvidenceRed, match="^" + reason + "$"):
            trusted.audit_publisher_from_github(
                client, event=event, expected_publisher_run_id=2002,
                expected_publisher_attempt=3, trusted_main_sha="c" * 40)


def test_r206_c_detects_pr_replay_during_rest_zip_fetch():
    client, event, _ = _r206_c_postcompletion_client()

    def rewrite_after_download(routes):
        routes[f"/repos/{REPO}/pulls/1087"]["head"]["sha"] = "d" * 40

    client.mutate = rewrite_after_download
    with __import__("pytest").raises(PostrunEvidenceRed, match="^POSTCOMPLETION_REST_RACE$"):
        trusted.audit_publisher_from_github(
            client, event=event, expected_publisher_run_id=2002,
            expected_publisher_attempt=3, trusted_main_sha="c" * 40)


MUTATIONS = [
    (lambda d: d["run"].__setitem__("status", "in_progress"), "RUN_NOT_SUCCESS"),
    (lambda d: d["run"].__setitem__("conclusion", "failure"), "RUN_NOT_SUCCESS"),
    (lambda d: d["run"].__setitem__("id", RUN + 1), "RUN_ID_MISMATCH"),
    (lambda d: d["run"].__setitem__("run_attempt", ATTEMPT + 1), "RUN_ATTEMPT_MISMATCH"),
    (lambda d: d["run"].__setitem__("head_sha", "c" * 40), "RUN_SHA_MISMATCH"),
    (lambda d: d["run"].__setitem__("path", ".github/workflows/evil.yml"), "RUN_WORKFLOW_MISMATCH"),
    (lambda d: d["run"]["repository"].__setitem__("full_name", "foreign/repo"), "RUN_REPO_MISMATCH"),
    (lambda d: d["run"].__setitem__("event", "workflow_dispatch"), "RUN_EVENT_MISMATCH"),
    (lambda d: d["pr"]["head"].__setitem__("sha", "d" * 40), "PR_HEAD_STALE"),
    (lambda d: d["pr"].__setitem__("number", 1086), "PR_IDENTITY_MISMATCH"),
    (lambda d: d["pr"]["base"].__setitem__("ref", "main"), "PR_BASE_MISMATCH"),
    (lambda d: d["commit"]["tree"].__setitem__("sha", "e" * 40), "MANIFEST_TREE_MISMATCH"),
    (lambda d: d["artifact"].__setitem__("id", ARTIFACT_ID + 1), "ARTIFACT_ID_MISMATCH"),
    (lambda d: d["artifact"].__setitem__("digest", "sha256:" + "f" * 64), "ARTIFACT_DIGEST_MISMATCH"),
    (lambda d: d["artifact"]["workflow_run"].__setitem__("id", RUN + 1), "ARTIFACT_RUN_MISMATCH"),
    (lambda d: d["artifact"].__setitem__("name", "porota-predeploy-v2-" + SHA), "ARTIFACT_NAME_MISMATCH"),
    (lambda d: d["artifact_list"].__setitem__("total_count", 2), "ARTIFACT_LIST_AMBIGUOUS"),
]


def test_wrong_run_pr_artifact_receipt_or_tree_fails_closed():
    for mutator, reason in MUTATIONS:
        evidence = _evidence()
        mutator(evidence)
        _red(evidence, reason)


def test_mutated_raw_zip_bytes_fail_even_when_zip_parser_would_accept_them():
    evidence = _evidence()
    altered = bytearray(evidence["zip_bytes"])
    altered[10] ^= 1
    evidence["zip_bytes"] = bytes(altered)
    _red(evidence, "ZIP_DIGEST_MISMATCH")



def test_zip32_seekable_and_signed_streaming_descriptors_both_accepted():
    seekable = _evidence()
    with zipfile.ZipFile(io.BytesIO(seekable["zip_bytes"])) as archive:
        assert all(not (item.flag_bits & 8) for item in archive.infolist())
    assert verify_postrun_bytes(**seekable)["release_go"] is False
    streaming = _evidence()
    _streaming_descriptor_zip(streaming)
    assert verify_postrun_bytes(**streaming)["status"] == "POSTRUN_ARTIFACT_BYTES_MATCH"
    assert verify_postrun_bytes(**streaming)["release_go"] is False


def test_zip32_public_artifact_cannot_carry_resealed_global_or_member_comments():
    hidden = b"PRIVATE_DOCKER_CONFIG_ENV_CANARY"
    for kwargs in (
        {"archive_comment": hidden},
        {"member_comment": hidden},
        {"member_extra": struct.pack("<HH", 0xCAFE, len(hidden)) + hidden},
    ):
        evidence = _evidence()
        _zip_with_metadata(evidence, **kwargs)
        _red(evidence, "ZIP_ENVELOPE_UNSAFE")


def test_zip32_public_artifact_rejects_resealed_prefix_and_eocd_trailer():
    hidden = b"PRIVATE_DOCKER_CONFIG_ENV_CANARY"
    for adapter in (
        lambda raw: hidden + raw,
        lambda raw: raw + hidden,
    ):
        evidence = _evidence()
        raw = adapter(evidence["zip_bytes"])
        _assert_readable_zip(raw)
        _reseal_zip_only(evidence, raw)
        _red(evidence, "ZIP_ENVELOPE_UNSAFE")


def test_zip32_public_artifact_rejects_resealed_precentral_and_interlocal_gaps():
    hidden = b"PRIVATE_DOCKER_CONFIG_ENV_CANARY"
    for interlocal in (False, True):
        evidence = _evidence()
        raw = evidence["zip_bytes"]
        if interlocal:
            _, _, _, at = _first_zip32_file_parts(raw)
        else:
            at = struct.unpack_from("<I", raw, len(raw) - 22 + 16)[0]
        modified = _inject_zip32_bytes(raw, at, hidden)
        _assert_readable_zip(modified)
        _reseal_zip_only(evidence, modified)
        _red(evidence, "ZIP_ENVELOPE_UNSAFE")


def test_zip32_public_artifact_rejects_resealed_local_extra_only():
    evidence = _evidence()
    raw = evidence["zip_bytes"]
    first, compressed_begin, _, _ = _first_zip32_file_parts(raw)
    payload = b"PRIVATE_DOCKER_CONFIG_ENV_CANARY"
    extra = struct.pack("<HH", 0xCAFE, len(payload)) + payload
    first_name_length = struct.unpack_from("<H", raw, first.header_offset + 26)[0]
    at = first.header_offset + 30 + first_name_length
    assert at == compressed_begin
    modified = _inject_zip32_bytes(raw, at, extra, local_extra=True)
    _assert_readable_zip(modified)
    _reseal_zip_only(evidence, modified)
    _red(evidence, "ZIP_ENVELOPE_UNSAFE")



def test_zip32_public_artifact_rejects_resealed_central_only_extra():
    evidence = _evidence()
    hidden = b"PRIVATE_DOCKER_CONFIG_ENV_CANARY"
    extra = struct.pack("<HH", 0xCAFE, len(hidden)) + hidden
    modified = _central_only_zip32_extra(evidence["zip_bytes"], extra)
    _assert_readable_zip(modified)
    _reseal_zip_only(evidence, modified)
    _red(evidence, "ZIP_ENVELOPE_UNSAFE")


def test_zip32_streaming_descriptor_gap_cannot_hide_private_inspect():
    evidence = _evidence()
    _streaming_descriptor_zip(evidence)
    _, _, _, next_local = _first_zip32_file_parts(evidence["zip_bytes"])
    modified = _inject_zip32_bytes(evidence["zip_bytes"], next_local,
                                   b"PRIVATE_DOCKER_CONFIG_ENV_CANARY")
    _assert_readable_zip(modified)
    _reseal_zip_only(evidence, modified)
    _red(evidence, "ZIP_ENVELOPE_UNSAFE")



def test_zip32_stored_members_require_exact_compressed_uncompressed_lengths():
    evidence = _evidence()
    _stored_zip(evidence)
    assert verify_postrun_bytes(**evidence)["status"] == "POSTRUN_ARTIFACT_BYTES_MATCH"
    first, _, at, _ = _first_zip32_file_parts(evidence["zip_bytes"])
    assert first.compress_type == zipfile.ZIP_STORED
    altered = _inject_zip32_bytes(evidence["zip_bytes"], at,
                                  b"PRIVATE_DOCKER_CONFIG_ENV_CANARY",
                                  stream_trailing=True)
    _assert_readable_zip(altered)
    _reseal_zip_only(evidence, altered)
    _red(evidence, "ZIP_STORED_STREAM_UNSAFE")


def test_zip32_public_artifact_rejects_resealed_unused_deflate_bytes():
    evidence = _evidence()
    raw = evidence["zip_bytes"]
    first, _, at, _ = _first_zip32_file_parts(raw)
    assert first.compress_type == zipfile.ZIP_DEFLATED and not first.flag_bits & 8
    modified = _inject_zip32_bytes(raw, at, b"PRIVATE_DOCKER_CONFIG_ENV_CANARY",
                                   stream_trailing=True)
    _assert_readable_zip(modified)
    _reseal_zip_only(evidence, modified)
    _red(evidence, "ZIP_DEFLATE_STREAM_UNSAFE")


def test_resealed_zip_member_corruption_and_ambiguous_members_are_rejected():
    for rename, mutate, symlink, reason in (
        ({"native.tar": "../native.tar"}, None, None, "ZIP_PATH_UNSAFE"),
        ({"native.tar": "custody.json"}, None, None, "ZIP_DUPLICATE_MEMBER"),
        (None, None, "native.tar", "ZIP_TYPE_UNSAFE"),
        (None, {"native.tar": b"\0" * (8 * 1024 * 1024)}, None, "ZIP_EXPANSION_LIMIT"),
        (None, {"native.tar": b"mutated"}, None, "FILE_SIZE_MISMATCH"),
    ):
        evidence = _evidence()
        _repack(evidence, rename=rename, mutate=mutate, symlink=symlink)
        _red(evidence, reason)


def test_resealed_native_tar_with_missing_required_file_fails_closed():
    evidence = _evidence()
    _reseal_native_tar(evidence, _tar(paths=NATIVE_PATHS[:-1]))
    _red(evidence, "TAR_SET_INVALID")



def test_native_tar_resealed_member_padding_cannot_carry_private_docker_env():
    evidence = _evidence()
    raw = bytearray(_tar())
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        first = next(iter(archive))
        visible = archive.extractfile(first).read()
    data_end = first.offset_data + first.size
    padding_end = first.offset_data + ((first.size + 511) // 512) * 512
    canary = b"PRIVATE_DOCKER_CONFIG_ENV_CANARY"
    assert len(visible) == first.size and data_end + len(canary) <= padding_end
    raw[data_end:data_end + len(canary)] = canary
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        item = next(iter(archive))
        assert archive.extractfile(item).read() == visible
    _reseal_native_tar(evidence, bytes(raw))
    _red(evidence, "TAR_CANONICAL_BYTES_MISMATCH")


def test_native_tar_resealed_ustar_uname_header_cannot_carry_private_env():
    evidence = _evidence()
    raw = bytearray(_tar())
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        first = next(iter(archive))
        contents = archive.extractfile(first).read()
    injected = tarfile.TarInfo(first.name)
    injected.uid = injected.gid = 1000
    injected.mode = 0o600
    injected.mtime = 0
    injected.size = len(contents)
    injected.uname = "PRIVATE_DOCKER_CONFIG_ENV_CANARY"
    raw[first.offset:first.offset + 512] = injected.tobuf(format=tarfile.USTAR_FORMAT)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        item = next(iter(archive))
        assert item.uname == injected.uname
        assert archive.extractfile(item).read() == contents
    _reseal_native_tar(evidence, bytes(raw))
    _red(evidence, "TAR_CANONICAL_BYTES_MISMATCH")


def test_native_tar_resealed_zero_extension_must_match_exact_ustar_writer():
    evidence = _evidence()
    raw = _tar() + bytes(10240)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        assert len(archive.getmembers()) == 13
    _reseal_native_tar(evidence, raw)
    _red(evidence, "TAR_CANONICAL_BYTES_MISMATCH")


def test_resealed_docker_inspect_cannot_escalate_host_or_collector_isolation():
    for file_name, mutator in (
        ("source-inspect.json", lambda i: i["HostConfig"].__setitem__("Privileged", True)),
        ("source-inspect.json", lambda i: i["HostConfig"].__setitem__("NetworkMode", "host")),
        ("source-inspect.json", lambda i: i["Config"].__setitem__("User", "0:0")),
        ("collector-inspect.json", lambda i: i["HostConfig"].__setitem__("VolumesFrom", [])),
    ):
        evidence = _evidence()
        _reseal_inspect(evidence, file_name, mutator)
        _red(evidence, "INSPECT_CONFIG_UNSAFE")


def test_public_v2_omits_all_docker_source_paths_and_never_claims_raw_replay():
    evidence = _evidence()
    with zipfile.ZipFile(io.BytesIO(evidence["zip_bytes"])) as archive:
        producer = json.loads(archive.read("source-inspect.json"))
        collector = json.loads(archive.read("collector-inspect.json"))
    for doc in (producer, collector):
        assert doc["schema"] == "RC6_R163_PUBLIC_INSPECT_V2"
        assert doc["raw_inspect"]["published"] is False
        assert doc["source_redaction"]["runtime_source_nonempty_observed"] is True
        assert "Source" not in doc["record"]["Mounts"][0]
        assert set(doc["record"]) == {"Id", "Image", "Config", "HostConfig", "Mounts"}
        assert set(doc["record"]["Config"]) == {"User", "Labels"}
        assert "Env" not in doc["record"]["Config"]
        assert "GraphDriver" not in doc["record"]
        assert "LogPath" not in doc["record"]
    assert producer["source_redaction"]["hostconfig_declaration"] == "EMPTY"
    assert "Source" not in producer["record"]["HostConfig"]["Mounts"][0]
    assert collector["source_redaction"]["hostconfig_declaration"] == "NOT_APPLICABLE"
    result = verify_postrun_bytes(**evidence)
    assert result["status"] == "POSTRUN_ARTIFACT_BYTES_MATCH"
    assert result["raw_inspect_contents_verified_by_this_guard"] is False
    assert result["automatic_go"] is False and result["release_go"] is False
    assert result["runtime_verified"] is False


def test_public_v2_moby_omitted_hostconfig_declaration_contract_is_explicit():
    evidence = _evidence()
    _reseal_inspect(
        evidence, "source-inspect.json",
        lambda doc: doc["source_redaction"].__setitem__("hostconfig_declaration", "ABSENT"),
        envelope=True,
    )
    result = verify_postrun_bytes(**evidence)
    assert result["raw_inspect_contents_verified_by_this_guard"] is False
    assert result["release_go"] is False



def test_public_v2_mount_mode_uses_observed_engine_28_0_4_physical_semantics():
    evidence = _evidence()
    with zipfile.ZipFile(io.BytesIO(evidence["zip_bytes"])) as archive:
        source = json.loads(archive.read("source-inspect.json"))
        collector = json.loads(archive.read("collector-inspect.json"))
    assert source["record"]["Mounts"][0]["Mode"] == "z"
    assert source["record"]["Mounts"][0]["RW"] is True
    assert collector["record"]["Mounts"][0]["Mode"] == ""
    assert collector["record"]["Mounts"][0]["RW"] is False
    assert verify_postrun_bytes(**evidence)["release_go"] is False
    for role, bad_mode in (
        ("source-inspect.json", ""), ("source-inspect.json", "ro"),
        ("source-inspect.json", "Z"), ("collector-inspect.json", "ro"),
        ("collector-inspect.json", "z"), ("collector-inspect.json", None),
    ):
        evidence = _evidence()
        _reseal_inspect(
            evidence, role,
            lambda record, value=bad_mode: record["Mounts"][0].__setitem__(
                "Mode", value
            ),
        )
        _red(evidence, "INSPECT_VOLUME_MISMATCH")


def test_public_v2_rejects_any_reintroduced_hostconfig_source_key():
    for source in ("", None, "named-volume", VOLUME):
        evidence = _evidence()
        _reseal_inspect(
            evidence, "source-inspect.json",
            lambda item, value=source: item["HostConfig"]["Mounts"][0].__setitem__("Source", value),
        )
        _red(evidence, "INSPECT_CONFIG_UNSAFE")


def test_public_v2_rejects_legacy_full_docker_inspect():
    evidence = _evidence()
    _reseal_inspect(
        evidence, "source-inspect.json",
        replacement=[_inspect(SOURCE_CID, collector=False)],
    )
    _red(evidence, "INSPECT_PROJECTION_SCHEMA_INVALID")


def test_public_v2_resealed_raw_fields_and_host_paths_fail_schema():
    for mutator in (
        lambda record: record["Config"].__setitem__("Env", ["PPI_TOKEN=CANARY_SECRET"]),
        lambda record: record.__setitem__("GraphDriver", {"Data": {"UpperDir": "/private/root"}}),
        lambda record: record.__setitem__("LogPath", "/private/secret.log"),
        lambda record: record.__setitem__("HostnamePath", "/private/hostname"),
        lambda record: record["HostConfig"].__setitem__("LogConfig", {"Config": {"token": "CANARY"}}),
        lambda record: record["Mounts"][0].__setitem__("Source", "/var/lib/docker/secret"),
        lambda record: record["Mounts"][0].__setitem__("Source", None),
    ):
        evidence = _evidence()
        _reseal_inspect(evidence, "source-inspect.json", mutator)
        _red(evidence, "INSPECT_PROJECTION_SCHEMA_INVALID")


def test_public_v2_fails_closed_on_schema_version_role_and_runtime_claims():
    for key, value in (
        ("schema", "RC6_R163_DOCKER_INSPECT_V1"),
        ("schema_version", True),
        ("schema_version", 1),
        ("role", "bundle-collector"),
        ("status", "RUNTIME_VERIFIED"),
        ("host_or_runtime_verified", True),
    ):
        evidence = _evidence()
        _reseal_inspect(
            evidence, "source-inspect.json",
            lambda doc, name=key, bad=value: doc.__setitem__(name, bad),
            envelope=True,
        )
        _red(evidence, "INSPECT_PROJECTION_SCHEMA_INVALID")


def test_public_v2_rejects_private_digest_or_source_attestation_abuse():
    for section, key, value, reason in (
        ("raw_inspect", "sha256", "g" * 64, "INSPECT_PRIVATE_DIGEST_UNVERIFIABLE"),
        ("raw_inspect", "size_bytes", 0, "INSPECT_PRIVATE_DIGEST_UNVERIFIABLE"),
        ("raw_inspect", "size_bytes", True, "INSPECT_PRIVATE_DIGEST_UNVERIFIABLE"),
        ("raw_inspect", "published", True, "INSPECT_PRIVATE_DIGEST_UNVERIFIABLE"),
        ("raw_inspect", "raw_path", "/private/secret", "INSPECT_PRIVATE_DIGEST_UNVERIFIABLE"),
        ("source_redaction", "hostconfig_declaration", "NAMED", "INSPECT_SOURCE_REDACTION_INVALID"),
        ("source_redaction", "runtime_source_nonempty_observed", False, "INSPECT_SOURCE_REDACTION_INVALID"),
        ("source_redaction", "private_source", "/private/secret", "INSPECT_SOURCE_REDACTION_INVALID"),
    ):
        evidence = _evidence()
        _reseal_inspect(
            evidence, "source-inspect.json",
            lambda doc, part=section, name=key, bad=value: doc[part].__setitem__(name, bad),
            envelope=True,
        )
        _red(evidence, reason)


def test_public_v2_private_raw_digest_is_not_independently_replayable():
    evidence = _evidence()
    _reseal_inspect(
        evidence, "source-inspect.json",
        lambda doc: doc["raw_inspect"].__setitem__("sha256", "f" * 64),
        envelope=True,
    )
    result = verify_postrun_bytes(**evidence)
    assert result["raw_inspect_contents_verified_by_this_guard"] is False
    assert result["native_host_custody_verified"] is False
    assert result["automatic_go"] is False and result["release_go"] is False


def test_same_run_wrong_artifact_attempt_name_and_size_fail_closed():
    for change, reason in (
        (lambda d: d["artifact"].__setitem__("name", f"rc6-r163-offhost-{RUN}-1-{SHA}"),
         "ARTIFACT_NAME_MISMATCH"),
        (lambda d: d["artifact"].__setitem__("size_in_bytes", 1),
         "ARTIFACT_SIZE_MISMATCH"),
        (lambda d: d["artifact"].__setitem__("expired", True),
         "ARTIFACT_EXPIRED"),
        (lambda d: d["event"]["workflow_run"].__setitem__("run_attempt", ATTEMPT + 1),
         "EVENT_RUN_MISMATCH"),
    ):
        evidence = _evidence()
        change(evidence)
        _red(evidence, reason)


def test_rerun_attempt_selects_only_unique_current_artifact_not_old_attempt():
    evidence = _evidence()
    previous = dict(evidence["artifact"],
                    id=ARTIFACT_ID - 1,
                    name=f"rc6-r163-offhost-{RUN}-1-{SHA}")
    evidence["artifact_list"]["total_count"] = 2
    evidence["artifact_list"]["artifacts"].insert(0, previous)
    verified = verify_postrun_bytes(**evidence)
    assert verified["artifact_id"] == ARTIFACT_ID
    assert verified["run_attempt"] == ATTEMPT


def test_duplicate_current_attempt_artifact_name_fails_closed():
    evidence = _evidence()
    impostor = dict(evidence["artifact"], id=ARTIFACT_ID + 1)
    evidence["artifact_list"]["artifacts"].append(impostor)
    evidence["artifact_list"]["total_count"] = 2
    _red(evidence, "ARTIFACT_LIST_AMBIGUOUS")


def test_missing_workflow_event_identity_fails_closed():
    evidence = _evidence()
    del evidence["event"]["workflow_run"]["id"]
    _red(evidence, "EVENT_RUN_MISMATCH")


def test_trusted_json_parser_rejects_duplicate_and_infinite_float():
    for data, reason in ((b'{"id":1,"id":2}', "JSON_DUPLICATE_KEY"),
                         (b'{"id":1,"unused":1e999}', "JSON_NONFINITE")):
        try:
            load_unique_json(data)
        except PostrunEvidenceRed as exc:
            assert str(exc) == reason, (str(exc), reason)
        else:
            raise AssertionError("EXPECTED_JSON_RED:" + reason)


class FakeGitHub:
    def __init__(self, evidence):
        self.evidence = evidence
        self.calls = []
        self.pr_call_count = 0
        self.change_pr_on_second_read = False
        self.attempt_call_count = 0
        self.change_attempt_on_second_read = False

    def get_json(self, path):
        self.calls.append(("json", path))
        if path.endswith("/actions/runs/991"):
            return self.evidence["run"]
        if path.endswith("/actions/runs/991/attempts/2"):
            self.attempt_call_count += 1
            if self.change_attempt_on_second_read and self.attempt_call_count >= 2:
                return dict(self.evidence["attempt"],
                            run_started_at="2026-11-12T03:04:12Z")
            return self.evidence["attempt"]
        if path.endswith("/pulls/1087"):
            self.pr_call_count += 1
            if self.change_pr_on_second_read and self.pr_call_count >= 2:
                modified = dict(self.evidence["pr"])
                modified["head"] = dict(modified["head"], sha="f" * 40)
                return modified
            return self.evidence["pr"]
        if path.endswith("/git/commits/" + SHA):
            return self.evidence["commit"]
        if path.endswith("/actions/runs/991/artifacts?per_page=100"):
            return self.evidence["artifact_list"]
        if path.endswith("/actions/artifacts/883"):
            return self.evidence["artifact"]
        raise AssertionError("UNEXPECTED_GITHUB_ENDPOINT")

    def get_zip(self, artifact_id):
        self.calls.append(("zip", artifact_id))
        assert artifact_id == ARTIFACT_ID
        return self.evidence["zip_bytes"]


def test_trusted_client_uses_rest_directly_and_rechecks_fresh_pr_after_zip():
    evidence = _evidence()
    client = FakeGitHub(evidence)
    result = audit_from_github(client, event=evidence["event"],
                               expected_run_id=RUN,
                               expected_run_attempt=ATTEMPT)
    assert result["status"] == "POSTRUN_ARTIFACT_BYTES_MATCH"
    assert client.pr_call_count == 2
    assert client.attempt_call_count == 2
    assert client.calls.count(("json", f"/repos/{REPO}/actions/runs/{RUN}/attempts/{ATTEMPT}")) == 2
    assert client.calls.count(("zip", ARTIFACT_ID)) == 1


def test_pr_head_moved_during_download_fails_closed_after_initial_match():
    evidence = _evidence()
    client = FakeGitHub(evidence)
    client.change_pr_on_second_read = True
    try:
        audit_from_github(client, event=evidence["event"],
                          expected_run_id=RUN, expected_run_attempt=ATTEMPT)
    except PostrunEvidenceRed as exc:
        assert str(exc) == "REST_RACE_RED", str(exc)
    else:
        raise AssertionError("STALE_PR_HEAD_NOT_REJECTED")



def test_future_attempt_named_artifact_created_in_previous_attempt_is_stale():
    evidence = _evidence_with_attempt()
    # All artifact/name/run/head/manifest/digest fields are valid for attempt 2,
    # but artifact.created_at belongs to attempt 1, prior to attempt 2 start.
    evidence["attempt"]["run_started_at"] = "2026-11-12T03:31:00Z"
    evidence["attempt"]["created_at"] = "2026-11-12T03:31:01Z"
    _red(evidence, "ARTIFACT_ATTEMPT_WINDOW_INVALID")


def test_same_second_as_attempt_start_is_ambiguous_and_fails_closed():
    evidence = _evidence_with_attempt()
    at_start = evidence["attempt"]["run_started_at"]
    for receipt in (evidence["artifact"], evidence["artifact_list"]["artifacts"][0]):
        receipt["created_at"] = at_start
    _red(evidence, "ARTIFACT_ATTEMPT_WINDOW_INVALID")


def test_attempt_receipt_identity_and_success_are_mandatory():
    for key, value, reason in (
        ("id", RUN + 1, "ATTEMPT_IDENTITY_MISMATCH"),
        ("run_attempt", ATTEMPT - 1, "ATTEMPT_IDENTITY_MISMATCH"),
        ("head_sha", "c" * 40, "ATTEMPT_IDENTITY_MISMATCH"),
        ("path", ".github/workflows/evil.yml", "ATTEMPT_IDENTITY_MISMATCH"),
        ("name", "different workflow", "ATTEMPT_IDENTITY_MISMATCH"),
        ("event", "workflow_dispatch", "ATTEMPT_IDENTITY_MISMATCH"),
        ("head_branch", "different-branch", "ATTEMPT_IDENTITY_MISMATCH"),
        ("repository", {"full_name": "other/repo"}, "ATTEMPT_IDENTITY_MISMATCH"),
        ("head_repository", {"full_name": "other/repo"}, "ATTEMPT_IDENTITY_MISMATCH"),
        ("status", "in_progress", "ATTEMPT_NOT_SUCCESS"),
        ("conclusion", "failure", "ATTEMPT_NOT_SUCCESS"),
    ):
        evidence = _evidence_with_attempt()
        evidence["attempt"][key] = value
        _red(evidence, reason)


def test_attempt_and_artifact_timestamp_malformed_missing_and_out_of_window_red():
    for location, key, value, reason in (
        ("attempt", "run_started_at", None, "ATTEMPT_TIMESTAMP_INVALID"),
        ("attempt", "run_started_at", "2026-11-12T03:04:13+00:00", "ATTEMPT_TIMESTAMP_INVALID"),
        ("attempt", "run_started_at", "2026-13-12T03:04:13Z", "ATTEMPT_TIMESTAMP_INVALID"),
        ("attempt", "updated_at", "2026-11-12T03:02:00Z", "ATTEMPT_TIMESTAMP_INVALID"),
        ("attempt", "created_at", "2026-11-12T03:40:00Z", "ATTEMPT_TIMESTAMP_INVALID"),
        ("artifact", "created_at", None, "ARTIFACT_TIMESTAMP_INVALID"),
        ("artifact", "created_at", "2026-11-12", "ARTIFACT_TIMESTAMP_INVALID"),
        ("artifact", "created_at", "2026-11-12T03:20:00-00:00", "ARTIFACT_TIMESTAMP_INVALID"),
        ("artifact", "updated_at", "2026-11-12T03:40:00Z", "ARTIFACT_ATTEMPT_WINDOW_INVALID"),
        ("artifact", "updated_at", "2026-11-12T03:00:00Z", "ARTIFACT_ATTEMPT_WINDOW_INVALID"),
    ):
        evidence = _evidence_with_attempt()
        evidence[location][key] = value
        if location == "artifact":
            evidence["artifact_list"]["artifacts"][0][key] = value
        _red(evidence, reason)



def test_artifact_inconsistent_or_future_creation_and_update_windows_red():
    for created, updated in (
        ("2026-11-12T03:36:00Z", "2026-11-12T03:34:00Z"),
        ("2026-11-12T03:30:03Z", "2026-11-12T03:30:02Z"),
    ):
        evidence = _evidence_with_attempt()
        for receipt in (evidence["artifact"], evidence["artifact_list"]["artifacts"][0]):
            receipt["created_at"] = created
            receipt["updated_at"] = updated
        _red(evidence, "ARTIFACT_ATTEMPT_WINDOW_INVALID")


def test_artifact_list_and_detail_timestamps_cannot_disagree():
    evidence = _evidence_with_attempt()
    evidence["artifact_list"]["artifacts"][0]["created_at"] = "2026-11-12T03:30:01Z"
    _red(evidence, "ARTIFACT_TIMESTAMP_MISMATCH")
    evidence = _evidence_with_attempt()
    del evidence["artifact_list"]["artifacts"][0]["updated_at"]
    _red(evidence, "ARTIFACT_TIMESTAMP_MISMATCH")


def test_attempt_metadata_changed_after_download_is_rest_race_red():
    evidence = _evidence_with_attempt()
    client = FakeGitHub(evidence)
    client.change_attempt_on_second_read = True
    try:
        audit_from_github(client, event=evidence["event"],
                          expected_run_id=RUN, expected_run_attempt=ATTEMPT)
    except PostrunEvidenceRed as exc:
        assert str(exc) == "REST_RACE_RED", str(exc)
    else:
        raise AssertionError("STALE_ATTEMPT_WINDOW_NOT_REJECTED")


def test_zip_redirect_must_be_https_host_allowlisted_and_no_auth_in_url():
    assert allowed_storage_redirect("https://results-receiver.actions.githubusercontent.com/opaque?q=1")
    assert allowed_storage_redirect("https://productionresultssa0.blob.core.windows.net/opaque?sig=1")
    for bad in ("http://productionresultssa0.blob.core.windows.net/evil",
                "https://127.0.0.1/secret", "https://user:password@objects.githubusercontent.com/zip",
                "https://api.evil.test/artifact", "file:///etc/passwd"):
        assert not allowed_storage_redirect(bad), bad


def test_signed_zip_redirect_never_receives_github_bearer_token():
    signed = "https://productionresultssa0.blob.core.windows.net/zip?sig=private"
    raw = _evidence()["zip_bytes"]
    requests = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self, count):
            return raw[:count]

    class Opener:
        def open(self, request, timeout=30):
            requests.append(request)
            if request.full_url.startswith("https://api.github.com/"):
                raise urllib.error.HTTPError(request.full_url, 302, "Found",
                                             {"Location": signed}, None)
            assert request.full_url == signed
            return Response()

    with mock.patch("scripts.rc6_r163_trusted_postrun_byte_audit.urllib.request.build_opener",
                    return_value=Opener()):
        downloaded = GitHubReadOnlyREST("read-only-secret-token").get_zip(ARTIFACT_ID)
    assert downloaded == raw
    assert requests[0].get_header("Authorization") == "Bearer read-only-secret-token"
    assert requests[1].get_header("Authorization") is None


def load_tests(loader, tests, pattern):
    """Make plain-function adversarial fixtures discoverable by stdlib unittest."""
    suite = unittest.TestSuite()
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            suite.addTest(unittest.FunctionTestCase(value, description=name))
    return suite
