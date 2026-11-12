"""Adversarial offline fixtures for a proposed trusted-main postrun byte audit.

These are synthetic REST replies and TAR/ZIP bytes; never a GitHub/runtime GO.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import stat
import struct
import tarfile
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


def _tar(paths=NATIVE_PATHS) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        for path in sorted(paths):
            data = ("R163 synthetic evidence for " + path).encode()
            info = tarfile.TarInfo("./" + path)
            info.uid = info.gid = 1000
            info.mode = 0o600
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _native(tar_bytes):
    files = [{"path": path, "size_bytes": len(("R163 synthetic evidence for " + path).encode()),
              "sha256": hashlib.sha256(("R163 synthetic evidence for " + path).encode()).hexdigest()}
             for path in sorted(NATIVE_PATHS)]
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
    return {"event": event, "run": run, "pr": pr,
            "commit": {"sha": SHA, "tree": {"sha": TREE}},
            "artifact_list": {"total_count": 1, "artifacts": [dict(artifact)]},
            "artifact": artifact, "zip_bytes": zip_bytes,
            "expected_run_id": RUN, "expected_run_attempt": ATTEMPT}


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

    def get_json(self, path):
        self.calls.append(("json", path))
        if path.endswith("/actions/runs/991"):
            return self.evidence["run"]
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
