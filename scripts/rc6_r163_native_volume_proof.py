"""R163 HITO D OFFHOST contracts for isolated native proof evidence.

Only verifies caller-supplied bytes.  It never invokes Docker, reads a host
socket, changes a filesystem, or establishes authentic runtime/host custody.
The caller must separately prove that CIDs came from its own Docker create,
that Docker is confined to an ephemeral GitHub-hosted runner, and that the
bytes uploaded and independently downloaded are identical.
"""

from __future__ import annotations

import argparse
import json
import hashlib
import io
import os
import re
import stat
import sys
import tarfile
from typing import Any


_CID = re.compile(r"[0-9a-f]{64}\Z")
_IMAGE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DECIMAL = re.compile(r"[1-9][0-9]*\Z")
_ROLE = re.compile(r"[a-z][a-z0-9-]{0,31}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")

NATIVE_EVIDENCE_PATHS = frozenset({
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
})
MAX_TAR_BYTES = 32 * 1024 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_PAYLOAD_BYTES = 24 * 1024 * 1024


class VolumeProofHold(ValueError):
    """A bounded failure signal; never include untrusted JSON in the message."""

    exit_code = 79

    def __init__(self, reason: str):
        super().__init__("RC6_R163_HOLD79:" + reason)
        self.reason = reason


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise VolumeProofHold(reason)


def _pairs_unique(pairs: list[tuple[str, Any]]) -> dict:
    output = {}
    for key, value in pairs:
        _require(key not in output, "DUPLICATE_JSON_KEY")
        output[key] = value
    return output


def _json_bytes(raw: bytes) -> Any:
    _require(type(raw) is bytes and 0 < len(raw) <= 262144, "INSPECT_BYTES_INVALID")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs_unique,
                          parse_constant=lambda _: (_require(False, "JSON_CONSTANT_INVALID")))
    except VolumeProofHold:
        raise
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise VolumeProofHold("INSPECT_JSON_INVALID") from exc


def _anonymous_volume_declaration(mounts: Any) -> bool:
    """Allow inert Docker 24 serialization keys, never active mount options."""
    if (type(mounts) is not list or len(mounts) != 1 or
            type(mounts[0]) is not dict):
        return False
    mount = mounts[0]
    allowed = {"Type", "Source", "Target", "ReadOnly", "Consistency",
               "BindOptions", "VolumeOptions", "TmpfsOptions"}
    if (not set(mount).issubset(allowed) or
            mount.get("Type") != "volume" or mount.get("Source") != "" or
            mount.get("Target") != "/app/data" or
            ("ReadOnly" in mount and mount["ReadOnly"] is not False) or
            mount.get("Consistency") not in (None, "", "default", "consistent") or
            mount.get("BindOptions") is not None or
            mount.get("TmpfsOptions") is not None):
        return False
    options = mount.get("VolumeOptions")
    if options is None:
        return True
    if type(options) is not dict or not set(options).issubset({
            "NoCopy", "Labels", "DriverConfig", "Subpath"}):
        return False
    return (("NoCopy" not in options or options["NoCopy"] is False) and
            options.get("Labels") in (None, {}) and
            options.get("DriverConfig") in (None, {}) and
            options.get("Subpath") in (None, ""))


def _inspect(raw: bytes, *, cid: str, image_id: str, role: str,
             candidate_sha: str, candidate_tree_sha: str,
             run_id: str, run_attempt: str,
             collector: bool, source_cid: str) -> str:
    result = _json_bytes(raw)
    _require(type(result) is list and len(result) == 1 and
             type(result[0]) is dict, "INSPECT_SHAPE")
    entry = result[0]
    _require(entry.get("Id") == cid and entry.get("Image") == image_id,
             "CID_IMAGE_IDENTITY")
    config, host = entry.get("Config"), entry.get("HostConfig")
    _require(type(config) is dict and type(host) is dict,
             "CONTAINER_CONFIG_MISSING")
    expected_labels = {
        "porota.commit": candidate_sha,
        "porota.candidate_tree": candidate_tree_sha,
        "porota.predeploy": "v2",
        "porota.native-proof": "v3",
        "porota.native-role": role + ("-collector" if collector else ""),
        "porota.run_id": run_id,
        "porota.run_attempt": run_attempt,
    }
    _require(config.get("User") == "1000:1000" and
             config.get("Labels") == expected_labels,
             "UID_LABEL_CONTRACT")
    _require(host.get("Privileged") is False and
             host.get("ReadonlyRootfs") is True and
             host.get("NetworkMode") == "none" and
             host.get("CapDrop") == ["ALL"] and
             host.get("CapAdd") in (None, []) and
             host.get("Devices") in (None, []) and
             host.get("Tmpfs") in (None, {}) and
             (host.get("PublishAllPorts") is None or
              host.get("PublishAllPorts") is False) and
             host.get("PortBindings") in (None, {}) and
             host.get("Links") in (None, []) and
             host.get("ExtraHosts") in (None, []) and
             host.get("SecurityOpt") == ["no-new-privileges"] and
             host.get("Binds") in (None, []), "CONTAINER_ISOLATION")
    # The compatible neutral Docker 24 representations here are private,
    # empty or omitted; live runner serialization remains to be verified.
    # Explicit host/container namespace,
    # supplementary groups, requested devices, alternate runtimes, cgroup
    # overrides and sysctls are outside this proof's authority. The absence
    # of an option is never interpreted as affirmative runtime provenance.
    allowed_host_config = {
        "IpcMode": (None, "", "private"),
        "UTSMode": (None, "", "private"),
        "PidMode": (None, "", "private"),
        "UsernsMode": (None, ""),
        "CgroupnsMode": (None, "", "private"),
        "GroupAdd": (None, []),
        "DeviceRequests": (None, []),
        "DeviceCgroupRules": (None, []),
        "Runtime": (None, "", "runc"),
        "Sysctls": (None, {}),
        "CgroupParent": (None, ""),
    }
    for field, accepted in allowed_host_config.items():
        _require(host.get(field) in accepted,
                 "CONTAINER_UNSAFE_HOSTCONFIG_" + field.upper())
    mounts_from = host.get("VolumesFrom")
    declared_mounts = host.get("Mounts")
    if collector:
        _require(mounts_from == [source_cid + ":ro"] and
                 declared_mounts in (None, []), "COLLECTOR_SOURCE_CID")
    else:
        _require(mounts_from in (None, []) and
                 _anonymous_volume_declaration(declared_mounts),
                 "ANONYMOUS_VOLUME_DECLARATION")
    mounts = entry.get("Mounts")
    _require(type(mounts) is list and len(mounts) == 1 and
             type(mounts[0]) is dict, "MOUNT_CARDINALITY")
    mount = mounts[0]
    _require(mount.get("Type") == "volume" and
             mount.get("Destination") == "/app/data" and
             type(mount.get("Name")) is str and
             _CID.fullmatch(mount["Name"]) is not None and
             type(mount.get("Source")) is str and
             mount["Source"] != "" and
             mount.get("Driver") == "local" and
             mount.get("RW") is (not collector) and
             mount.get("Mode") == ("ro" if collector else ""),
             "MOUNT_VOLUME_IDENTITY")
    return mount["Name"]


def verify_volume_chain(*, producer_inspect_json: bytes,
                        collector_inspect_json: bytes, source_cid: str,
                        collector_cid: str, candidate_sha: str,
                        candidate_tree_sha: str, image_id: str,
                        run_id: str, run_attempt: str, role: str) -> dict:
    """Check exact native/collector CIDs and one anonymous RW→RO volume.

    JSON inspections alone are untrusted descriptions, not creation receipts.
    Returned OFFHOST state must never be treated as permission to deploy.
    """
    _require(type(source_cid) is str and _CID.fullmatch(source_cid) is not None and
             type(collector_cid) is str and _CID.fullmatch(collector_cid) is not None and
             source_cid != collector_cid, "EXPECTED_CIDS_INVALID")
    _require(type(candidate_sha) is str and _SHA.fullmatch(candidate_sha) is not None and
             type(candidate_tree_sha) is str and _SHA.fullmatch(candidate_tree_sha) is not None and
             type(image_id) is str and _IMAGE.fullmatch(image_id) is not None and
             type(run_id) is str and _DECIMAL.fullmatch(run_id) is not None and
             type(run_attempt) is str and _DECIMAL.fullmatch(run_attempt) is not None and
             type(role) is str and _ROLE.fullmatch(role) is not None,
             "EXPECTED_IDENTITY_INVALID")
    volume = _inspect(producer_inspect_json, cid=source_cid,
                      image_id=image_id, role=role, candidate_sha=candidate_sha,
                      candidate_tree_sha=candidate_tree_sha,
                      run_id=run_id, run_attempt=run_attempt, collector=False,
                      source_cid=source_cid)
    other_volume = _inspect(collector_inspect_json, cid=collector_cid,
                            image_id=image_id, role=role, candidate_sha=candidate_sha,
                            candidate_tree_sha=candidate_tree_sha,
                            run_id=run_id, run_attempt=run_attempt, collector=True,
                            source_cid=source_cid)
    _require(volume == other_volume, "VOLUME_MISMATCH")
    return {
        "schema_version": 1,
        "status": "OFFHOST_CONTRACT_ONLY",
        "candidate_sha": candidate_sha,
        "candidate_tree_sha": candidate_tree_sha,
        "image_id": image_id,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "role": role,
        "source_cid": source_cid,
        "collector_cid": collector_cid,
        "volume_name": volume,
        "mount_target": "/app/data",
        "uid": 1000,
        "gid": 1000,
        "host_or_runtime_verified": False,
    }


_CUSTODY_KEYS = frozenset({
    "schema_version", "status", "candidate_sha", "candidate_tree_sha",
    "image_id", "run_id", "run_attempt", "role", "source_cid",
    "collector_cid", "volume_name", "mount_target", "uid", "gid",
    "host_or_runtime_verified",
})


def _validate_custody_receipt(receipt: Any) -> None:
    _require(type(receipt) is dict and set(receipt) == _CUSTODY_KEYS,
             "CUSTODY_RECEIPT_SHAPE")
    _require(type(receipt["schema_version"]) is int and
             receipt["schema_version"] == 1 and
             receipt["status"] == "OFFHOST_CONTRACT_ONLY" and
             type(receipt["host_or_runtime_verified"]) is bool and
             receipt["host_or_runtime_verified"] is False and
             type(receipt["uid"]) is int and receipt["uid"] == 1000 and
             type(receipt["gid"]) is int and receipt["gid"] == 1000 and
             receipt["mount_target"] == "/app/data", "CUSTODY_RECEIPT_STATUS")
    _require(all(type(receipt[key]) is str and regex.fullmatch(receipt[key]) is not None
                 for key, regex in (
                     ("candidate_sha", _SHA), ("candidate_tree_sha", _SHA),
                     ("image_id", _IMAGE), ("run_id", _DECIMAL),
                     ("run_attempt", _DECIMAL), ("role", _ROLE),
                     ("source_cid", _CID), ("collector_cid", _CID),
                     ("volume_name", _CID))) and
             receipt["source_cid"] != receipt["collector_cid"],
             "CUSTODY_RECEIPT_IDENTITY")


def _native_tar_files(raw: bytes) -> list[dict]:
    _require(type(raw) is bytes and 1024 <= len(raw) <= MAX_TAR_BYTES and
             len(raw) % 512 == 0, "TAR_LENGTH_INVALID")
    _require(raw[-1024:] == bytes(1024), "TAR_TRAILER_MISSING")
    found: dict[str, dict] = {}
    cursor = 0
    total_payload = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            for item in archive:
                # Reject PAX/longname or hidden extra TAR records that might
                # change name/metadata interpretation in another extractor.
                _require(item.offset == cursor and item.offset_data == cursor + 512 and
                         not item.pax_headers, "TAR_HEADER_CHAIN")
                _require(type(item.size) is int and 0 <= item.size <= MAX_FILE_BYTES,
                         "TAR_FILE_SIZE")
                cursor = item.offset_data + ((item.size + 511) // 512) * 512
                _require(cursor <= len(raw), "TAR_TRUNCATED")
                name = item.name[2:] if item.name.startswith("./") else item.name
                _require(type(name) is str and "\\" not in name and
                         not name.startswith("/") and
                         "//" not in name and "\x00" not in name and
                         ".." not in name.split("/"), "TAR_PATH_UNSAFE")
                _require(item.isfile() and name in NATIVE_EVIDENCE_PATHS and
                         name not in found and item.linkname == "",
                         "TAR_FILE_UNEXPECTED")
                _require(item.uid == 1000 and item.gid == 1000 and
                         item.mode & 0o7133 == 0, "TAR_UID_MODE")
                _require(item.size > 0, "TAR_EMPTY_FILE")
                total_payload += item.size
                _require(total_payload <= MAX_PAYLOAD_BYTES, "TAR_PAYLOAD_LIMIT")
                stream = archive.extractfile(item)
                _require(stream is not None, "TAR_CONTENT_MISSING")
                data = stream.read(item.size + 1)
                _require(len(data) == item.size, "TAR_CONTENT_TRUNCATED")
                found[name] = {
                    "path": name,
                    "size_bytes": item.size,
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
    except (tarfile.TarError, EOFError, OSError, OverflowError) as exc:
        raise VolumeProofHold("TAR_PARSE_INVALID") from exc
    _require(len(raw) - cursor >= 1024 and
             not any(raw[cursor:]), "TAR_TRAILING_DATA")
    _require(set(found) == NATIVE_EVIDENCE_PATHS, "TAR_FILE_SET_INCOMPLETE")
    return [found[name] for name in sorted(found)]


def attest_collector_tar(raw: bytes, *, custody_receipt: dict) -> dict:
    """Seal exact collector TAR bytes and a fixed, 13-file native allowlist.

    This is an OFFHOST receipt.  It is not an artifact or authentic test proof
    until a separately trusted job independently downloads and verifies bytes.
    """
    _validate_custody_receipt(custody_receipt)
    files = _native_tar_files(raw)
    return {
        "schema": "RC6_R163_NATIVE_TAR_V1",
        "schema_version": 1,
        "status": "OFFHOST_BYTES_ONLY",
        "candidate_sha": custody_receipt["candidate_sha"],
        "candidate_tree_sha": custody_receipt["candidate_tree_sha"],
        "image_id": custody_receipt["image_id"],
        "run_id": custody_receipt["run_id"],
        "run_attempt": custody_receipt["run_attempt"],
        "role": custody_receipt["role"],
        "source_cid": custody_receipt["source_cid"],
        "collector_cid": custody_receipt["collector_cid"],
        "volume_name": custody_receipt["volume_name"],
        "tar_sha256": hashlib.sha256(raw).hexdigest(),
        "tar_size_bytes": len(raw),
        "file_count": len(files),
        "files": files,
        "host_or_runtime_verified": False,
    }


def _strict_equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(
            _strict_equal(left[key], right[key]) for key in left)
    if type(left) is list:
        return len(left) == len(right) and all(
            _strict_equal(a, b) for a, b in zip(left, right))
    return left == right


def verify_collector_manifest(raw: bytes, manifest_json: bytes, *,
                              expected_candidate_sha: str,
                              expected_candidate_tree_sha: str,
                              expected_run_id: str,
                              expected_run_attempt: str,
                              expected_source_cid: str,
                              expected_collector_cid: str,
                              expected_volume_name: str,
                              expected_image_id: str,
                              expected_role: str) -> dict:
    """Recompute every digest/size from bytes and compare a claimed manifest.

    The caller must still bind expected SHA/tree/run/CIDs and artifact ID to
    trusted GitHub evidence; matching an untrusted manifest is not such proof.
    """
    claimed = _json_bytes(manifest_json)
    _require(type(claimed) is dict and
             claimed.get("schema") == "RC6_R163_NATIVE_TAR_V1" and
             type(claimed.get("schema_version")) is int and
             claimed["schema_version"] == 1, "MANIFEST_SCHEMA")
    expected = {
        "candidate_sha": expected_candidate_sha,
        "candidate_tree_sha": expected_candidate_tree_sha,
        "run_id": expected_run_id,
        "run_attempt": expected_run_attempt,
        "source_cid": expected_source_cid,
        "collector_cid": expected_collector_cid,
        "volume_name": expected_volume_name,
        "image_id": expected_image_id,
        "role": expected_role,
    }
    _require(all(type(value) is str and claimed.get(key) == value
                 for key, value in expected.items()),
             "MANIFEST_TRUSTED_IDENTITY")
    receipt = {key: claimed.get(key) for key in _CUSTODY_KEYS}
    receipt.update({"schema_version": 1, "status": "OFFHOST_CONTRACT_ONLY",
                    "mount_target": "/app/data", "uid": 1000, "gid": 1000})
    _validate_custody_receipt(receipt)
    recomputed = attest_collector_tar(raw, custody_receipt=receipt)
    _require(_strict_equal(recomputed, claimed), "MANIFEST_BYTES_MISMATCH")
    return recomputed


def canonical_manifest_bytes(manifest: dict) -> bytes:
    """Stable UTF-8 JSON for the OFFHOST manifest; not a trust bootstrap."""
    _require(type(manifest) is dict and
             manifest.get("schema") == "RC6_R163_NATIVE_TAR_V1" and
             type(manifest.get("schema_version")) is int and
             manifest["schema_version"] == 1, "MANIFEST_SCHEMA")
    return (json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def _safe_read_proof(root_fd: int, relative: str) -> bytes:
    """Open from one directory fd, refusing symlinks even in parent paths."""
    fds: list[int] = []
    current = root_fd
    try:
        parts = relative.split("/")
        for directory in parts[:-1]:
            opened = os.open(directory, os.O_RDONLY | os.O_DIRECTORY |
                             os.O_NOFOLLOW, dir_fd=current)
            fds.append(opened)
            current = opened
        opened = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW |
                         os.O_NONBLOCK, dir_fd=current)
        fds.append(opened)
        before = os.fstat(opened)
        _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
                 before.st_uid == 1000 and before.st_gid == 1000 and
                 before.st_mode & 0o7133 == 0 and
                 0 < before.st_size <= MAX_FILE_BYTES, "PACK_FILE_IDENTITY")
        chunks: list[bytes] = []
        remaining = before.st_size + 1
        while remaining:
            chunk = os.read(opened, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        contents = b"".join(chunks)
        after = os.fstat(opened)
        _require(len(contents) == before.st_size and
                 (before.st_ino, before.st_dev, before.st_mode, before.st_uid,
                  before.st_gid, before.st_size, before.st_mtime_ns,
                  before.st_ctime_ns) ==
                 (after.st_ino, after.st_dev, after.st_mode, after.st_uid,
                  after.st_gid, after.st_size, after.st_mtime_ns,
                  after.st_ctime_ns), "PACK_CONTENT_CHANGED")
        return contents
    except OSError as exc:
        raise VolumeProofHold("PACK_PATH_INVALID") from exc
    finally:
        for fd in reversed(fds):
            os.close(fd)


def pack_collector_tar(proof_root: str) -> bytes:
    """Collector-side deterministic USTAR, strictly 13 paths, UID1000 only.

    This helper runs *inside* the unprivileged collector whose `/app/data`
    is inherited as an anonymous read-only Docker volume. It never invokes
    Docker or accesses another host filesystem; the caller must prove its
    container/volume custody independently.
    """
    _require(os.geteuid() == 1000 and os.getegid() == 1000, "PACK_UID_INVALID")
    _require(type(proof_root) is str and proof_root != "", "PACK_ROOT_INVALID")
    try:
        root_fd = os.open(proof_root, os.O_RDONLY | os.O_DIRECTORY |
                          os.O_NOFOLLOW)
    except OSError as exc:
        raise VolumeProofHold("PACK_ROOT_INVALID") from exc
    try:
        payload = io.BytesIO()
        total = 0
        with tarfile.open(fileobj=payload, mode="w",
                          format=tarfile.USTAR_FORMAT) as archive:
            for relative in sorted(NATIVE_EVIDENCE_PATHS):
                contents = _safe_read_proof(root_fd, relative)
                total += len(contents)
                _require(total <= MAX_PAYLOAD_BYTES, "PACK_PAYLOAD_LIMIT")
                info = tarfile.TarInfo("./" + relative)
                info.uid = 1000
                info.gid = 1000
                info.mode = 0o600
                info.mtime = 0
                info.size = len(contents)
                archive.addfile(info, io.BytesIO(contents))
        raw = payload.getvalue()
        _native_tar_files(raw)
        return raw
    finally:
        os.close(root_fd)


def _read_bounded(path: str, max_size: int) -> bytes:
    """Read a regular CLI input without following its final symlink."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        metadata = os.fstat(fd)
        _require(stat.S_ISREG(metadata.st_mode) and
                 0 < metadata.st_size <= max_size, "INPUT_FILE_INVALID")
        chunks: list[bytes] = []
        remaining = max_size + 1
        while remaining:
            piece = os.read(fd, min(1024 * 1024, remaining))
            if not piece:
                break
            chunks.append(piece)
            remaining -= len(piece)
        result = b"".join(chunks)
        _require(len(result) == metadata.st_size, "INPUT_FILE_CHANGED")
        return result
    finally:
        os.close(fd)


def main(argv: list[str] | None = None) -> int:
    """Small, offline CLI; no Docker calls, actions, uploads or host writes."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    pack = commands.add_parser("pack", help="collector-side fixed USTAR stdout")
    pack.add_argument("--proof-root", required=True)
    inspect = commands.add_parser("inspect", help="Docker inspect bytes -> custody stdout")
    for option in ("producer-inspect", "collector-inspect", "source-cid",
                   "collector-cid", "candidate-sha", "candidate-tree-sha",
                   "image-id", "run-id", "run-attempt", "role"):
        inspect.add_argument("--" + option, required=True)
    attest = commands.add_parser("attest", help="collector TAR -> manifest stdout")
    attest.add_argument("--tar", required=True)
    attest.add_argument("--custody", required=True)
    verify = commands.add_parser("verify", help="recheck claimed manifest & downloaded TAR")
    verify.add_argument("--tar", required=True)
    verify.add_argument("--manifest", required=True)
    for option in ("candidate-sha", "candidate-tree-sha", "run-id",
                   "run-attempt", "source-cid", "collector-cid",
                   "volume-name", "image-id", "role"):
        verify.add_argument("--expected-" + option, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "pack":
            result = pack_collector_tar(args.proof_root)
        elif args.command == "inspect":
            result = _canonical_custody_bytes(verify_volume_chain(
                producer_inspect_json=_read_bounded(args.producer_inspect, 262144),
                collector_inspect_json=_read_bounded(args.collector_inspect, 262144),
                source_cid=args.source_cid, collector_cid=args.collector_cid,
                candidate_sha=args.candidate_sha,
                candidate_tree_sha=args.candidate_tree_sha,
                image_id=args.image_id, run_id=args.run_id,
                run_attempt=args.run_attempt, role=args.role))
        elif args.command == "attest":
            receipt = _json_bytes(_read_bounded(args.custody, 262144))
            result = canonical_manifest_bytes(attest_collector_tar(
                _read_bounded(args.tar, MAX_TAR_BYTES), custody_receipt=receipt))
        else:
            result = canonical_manifest_bytes(verify_collector_manifest(
                _read_bounded(args.tar, MAX_TAR_BYTES),
                _read_bounded(args.manifest, 262144),
                **{key: getattr(args, key) for key in (
                    "expected_candidate_sha", "expected_candidate_tree_sha",
                    "expected_run_id", "expected_run_attempt",
                    "expected_source_cid", "expected_collector_cid",
                    "expected_volume_name", "expected_image_id", "expected_role")}))
        sys.stdout.buffer.write(result)
        return 0
    except VolumeProofHold as exc:
        sys.stderr.write(str(exc) + "\n")
        return 79
    except (OSError, OverflowError, TypeError, ValueError):
        sys.stderr.write("RC6_R163_HOLD79:INPUT_IO_INVALID\n")
        return 79


def _canonical_custody_bytes(receipt: dict) -> bytes:
    """Deterministic OFFHOST custody receipt JSON, separate from TAR manifest."""
    return (json.dumps(receipt, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


if __name__ == "__main__":
    raise SystemExit(main())
