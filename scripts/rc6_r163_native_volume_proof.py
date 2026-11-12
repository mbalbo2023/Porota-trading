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
import math
import hashlib
import io
import os
import re
import stat
import sys
import tarfile
import zipfile as _zipfile
import secrets as _secrets
import xml.etree.ElementTree as _etree
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
            mount.get("Type") != "volume" or mount.get("Source", "") != "" or
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
    # Log only fixed-name boolean checks, never inspect values or Docker paths.
    # The original predicates and fail-closed HOLD79 remain unchanged.
    checks = (
        ("type", mount.get("Type") == "volume"),
        ("target", mount.get("Destination") == "/app/data"),
        ("name", type(mount.get("Name")) is str and
         _CID.fullmatch(mount["Name"]) is not None),
        ("source", type(mount.get("Source")) is str and
         mount["Source"] != ""),
        ("driver", mount.get("Driver") == "local"),
        ("rw", mount.get("RW") is (not collector)),
        # Engine 28.0.4 Linux physical R186: local anonymous --mount
        # reports SELinux shared Mode="z"; --volumes-from CID:ro
        # reports Mode="" while the independent RW flag is False.
        # Never infer write access or readonly authority from Mode alone.
        ("mode", mount.get("Mode") == ("" if collector else "z")),
    )
    all_ok = all(ok for _, ok in checks)
    if not all_ok:
        print(
            "RC6_R163_MOUNT_SUBCHECKS=role=" +
            ("collector" if collector else "producer") + "|" +
            "|".join(name + "=" + str(int(ok)) for name, ok in checks),
            file=sys.stderr,
        )
    _require(all_ok, "MOUNT_VOLUME_IDENTITY")
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


# R203 public producer projection. This is a privacy boundary, not a new
# source of host/Docker trust. The independently pinned trusted-main parser
# maintains a separate allowlist for the same versioned artifact schema.
PUBLIC_INSPECT_SCHEMA = "RC6_R163_PUBLIC_INSPECT_V2"
_PUBLIC_INSPECT_KEYS = frozenset({
    "schema", "schema_version", "role", "status", "raw_inspect",
    "source_redaction", "record", "host_or_runtime_verified",
})
_PUBLIC_RECORD_KEYS = frozenset({"Id", "Image", "Config", "HostConfig", "Mounts"})
_PUBLIC_MOUNT_KEYS = frozenset({
    "Type", "Name", "Driver", "Destination", "Mode", "RW",
})
_PUBLIC_HOST_KEYS = frozenset({
    "Privileged", "ReadonlyRootfs", "NetworkMode", "CapDrop", "CapAdd",
    "Devices", "Tmpfs", "PublishAllPorts", "PortBindings", "Links",
    "ExtraHosts", "SecurityOpt", "Binds", "IpcMode", "UTSMode",
    "PidMode", "UsernsMode", "CgroupnsMode", "GroupAdd", "DeviceRequests",
    "DeviceCgroupRules", "Runtime", "Sysctls", "CgroupParent",
    "VolumesFrom", "Mounts",
})
_PRIVACY_SECRET_RE = re.compile(
    rb"(?i)(?:CANARY_SECRET|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    rb"github_pat_[A-Za-z0-9_]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|"
    rb"(?:authorization|proxy-authorization)\s*[\"']?\s*[:=]\s*[\"']?"
    rb"(?:bearer|basic)\s+[A-Za-z0-9._~-]{8,}|"
    rb"(?:api[_-]?key|client[_-]?secret|access[_-]?token|"
    rb"refresh[_-]?token|password|passwd|private[_-]?key)"
    rb"\s*[\"']?\s*[:=]\s*[\"']?[^\s\"',}]{4,}|"
    rb"/var/lib/docker/|/home/runner/work/|"
    rb"https?://[^\s\"'/]+:[^\s\"'@/]+@)"
)
_PRIVACY_JSON_DENIED_KEYS = frozenset({
    "authorization", "proxy-authorization", "password", "passwd",
    "api_key", "apikey", "client_secret", "access_token", "refresh_token",
    "private_key", "secret_key", "github_token", "ppi_token",
})


def _public_inspect_record(private_raw: bytes, *, collector: bool) -> dict:
    parsed = _json_bytes(private_raw)
    _require(type(parsed) is list and len(parsed) == 1 and
             type(parsed[0]) is dict, "PUBLIC_PRIVATE_INSPECT_SHAPE")
    entry = parsed[0]
    host, config, mounts = entry.get("HostConfig"), entry.get("Config"), entry.get("Mounts")
    # The mandatory private verifier has already checked every field below.
    _require(type(host) is dict and type(config) is dict and
             type(mounts) is list and len(mounts) == 1 and
             type(mounts[0]) is dict, "PUBLIC_PRIVATE_INSPECT_SHAPE")
    safe_host = {key: value for key, value in host.items() if key in _PUBLIC_HOST_KEYS}
    declaration = "NOT_APPLICABLE"
    if not collector:
        declared = safe_host["Mounts"][0]
        _require(type(declared) is dict, "PUBLIC_PRIVATE_MOUNT_SHAPE")
        declaration = "EMPTY" if "Source" in declared else "ABSENT"
        # Source is private even when the Docker declaration is empty.
        safe_host["Mounts"] = [{key: value for key, value in declared.items()
                               if key != "Source"}]
    projected = {
        "schema": PUBLIC_INSPECT_SCHEMA,
        "schema_version": 2,
        "role": "bundle-collector" if collector else "bundle",
        "status": "OFFHOST_PROJECTED_EVIDENCE_ONLY",
        "raw_inspect": {
            "sha256": hashlib.sha256(private_raw).hexdigest(),
            "size_bytes": len(private_raw),
            "published": False,
        },
        "source_redaction": {
            "hostconfig_declaration": declaration,
            "runtime_source_nonempty_observed": True,
        },
        "record": {
            "Id": entry["Id"],
            "Image": entry["Image"],
            "Config": {"User": config["User"], "Labels": config["Labels"]},
            "HostConfig": safe_host,
            "Mounts": [{key: mounts[0][key] for key in _PUBLIC_MOUNT_KEYS}],
        },
        "host_or_runtime_verified": False,
    }
    return projected


def _canonical_public_inspect_bytes(document: dict) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def project_public_inspects_v2(*, producer_inspect_json: bytes,
                               collector_inspect_json: bytes,
                               source_cid: str, collector_cid: str,
                               candidate_sha: str, candidate_tree_sha: str,
                               image_id: str, run_id: str, run_attempt: str,
                               role: str) -> tuple[bytes, bytes]:
    """Verify the PRIVATE Docker inspect pair, then disclose a safe V2 projection.

    Neither raw JSON, raw Docker mount paths, environment, driver state nor log
    paths leave the ephemeral runner. SHA256/size are producer commitments only.
    """
    _require(role == "bundle", "PUBLIC_ROLE_UNSUPPORTED")
    identity = {
        "source_cid": source_cid, "collector_cid": collector_cid,
        "candidate_sha": candidate_sha, "candidate_tree_sha": candidate_tree_sha,
        "image_id": image_id, "run_id": run_id,
        "run_attempt": run_attempt, "role": role,
    }
    verify_volume_chain(producer_inspect_json=producer_inspect_json,
                        collector_inspect_json=collector_inspect_json, **identity)
    projected = (
        _canonical_public_inspect_bytes(_public_inspect_record(
            producer_inspect_json, collector=False)),
        _canonical_public_inspect_bytes(_public_inspect_record(
            collector_inspect_json, collector=True)),
    )
    verify_public_inspects_v2(producer_public_json=projected[0],
                              collector_public_json=projected[1],
                              **identity)
    return projected


def _public_to_private_surrogate(raw: bytes, *, collector: bool) -> bytes:
    """Strictly parse allowlisted claims; the surrogate is NOT Docker raw proof."""
    document = _json_bytes(raw)
    _require(type(document) is dict and
             set(document) == _PUBLIC_INSPECT_KEYS and
             document["schema"] == PUBLIC_INSPECT_SCHEMA and
             type(document["schema_version"]) is int and
             document["schema_version"] == 2 and
             document["role"] == ("bundle-collector" if collector else "bundle") and
             document["status"] == "OFFHOST_PROJECTED_EVIDENCE_ONLY" and
             document["host_or_runtime_verified"] is False,
             "PUBLIC_INSPECT_SCHEMA")
    private = document["raw_inspect"]
    _require(type(private) is dict and
             set(private) == {"sha256", "size_bytes", "published"} and
             type(private["sha256"]) is str and
             _DIGEST.fullmatch(private["sha256"]) is not None and
             type(private["size_bytes"]) is int and
             0 < private["size_bytes"] <= 262144 and
             private["published"] is False, "PUBLIC_RAW_DIGEST_INVALID")
    redaction = document["source_redaction"]
    _require(type(redaction) is dict and
             set(redaction) == {"hostconfig_declaration",
                                "runtime_source_nonempty_observed"} and
             redaction["hostconfig_declaration"] in (
                 ("NOT_APPLICABLE",) if collector else ("ABSENT", "EMPTY")) and
             redaction["runtime_source_nonempty_observed"] is True,
             "PUBLIC_SOURCE_REDACTION_INVALID")
    record = document["record"]
    _require(type(record) is dict and set(record) == _PUBLIC_RECORD_KEYS and
             type(record["Config"]) is dict and
             set(record["Config"]) == {"User", "Labels"} and
             type(record["HostConfig"]) is dict and
             set(record["HostConfig"]).issubset(_PUBLIC_HOST_KEYS) and
             type(record["Mounts"]) is list and
             len(record["Mounts"]) == 1 and
             type(record["Mounts"][0]) is dict and
             set(record["Mounts"][0]) == _PUBLIC_MOUNT_KEYS,
             "PUBLIC_RECORD_ALLOWLIST")
    safe_host = record["HostConfig"]
    if not collector:
        declaration = safe_host.get("Mounts")
        _require(type(declaration) is list and len(declaration) == 1 and
                 type(declaration[0]) is dict and
                 "Source" not in declaration[0], "PUBLIC_MOUNT_SOURCE_PRESENT")
    # A fixed surrogate satisfies the pre-existing pure contract verifier.
    # Its synthetic Source cannot establish host/runtime custody or attest
    # to the unpublished raw SHA256 preimage; trusted-main is independent.
    surrogate = {
        "Id": record["Id"], "Image": record["Image"],
        "Config": record["Config"], "HostConfig": safe_host,
        "Mounts": [{**record["Mounts"][0],
                    "Source": "/redacted-untrusted-projection"}],
    }
    return json.dumps([surrogate], sort_keys=True,
                      separators=(",", ":"), ensure_ascii=True).encode("ascii")


def verify_public_inspects_v2(*, producer_public_json: bytes,
                              collector_public_json: bytes,
                              source_cid: str, collector_cid: str,
                              candidate_sha: str, candidate_tree_sha: str,
                              image_id: str, run_id: str, run_attempt: str,
                              role: str) -> dict:
    """Check public V2 semantics without pretending raw Docker replay."""
    _require(role == "bundle", "PUBLIC_ROLE_UNSUPPORTED")
    return verify_volume_chain(
        producer_inspect_json=_public_to_private_surrogate(
            producer_public_json, collector=False),
        collector_inspect_json=_public_to_private_surrogate(
            collector_public_json, collector=True),
        source_cid=source_cid, collector_cid=collector_cid,
        candidate_sha=candidate_sha, candidate_tree_sha=candidate_tree_sha,
        image_id=image_id, run_id=run_id,
        run_attempt=run_attempt, role=role,
    )


def _privacy_json_tree(value: Any, *, depth: int, budget: list[int]) -> None:
    _require(depth <= 32 and budget[0] > 0, "PRIVACY_JSON_DEPTH_OR_SIZE")
    budget[0] -= 1
    if type(value) is dict:
        for key, child in value.items():
            _require(type(key) is str and
                     key.lower() not in _PRIVACY_JSON_DENIED_KEYS,
                     "PRIVACY_JSON_SECRET_KEY")
            _privacy_json_tree(child, depth=depth + 1, budget=budget)
    elif type(value) is list:
        for child in value:
            _privacy_json_tree(child, depth=depth + 1, budget=budget)
    elif type(value) is str:
        _require(_PRIVACY_SECRET_RE.search(value.encode("utf-8")) is None,
                 "PRIVACY_JSON_DECODED_SECRET")
    else:
        _require(value is None or type(value) in (int, float, bool),
                 "PRIVACY_JSON_TYPE")


_R206_PUBLIC_XML_SCHEMA = "RC6_R206_PUBLIC_JUNIT_V1"
_R206_PUBLIC_JSON_SCHEMA = "RC6_R206_PUBLIC_JSON_V1"
_R206_PRIVATE_STATUS = "PRIVATE_SOURCE_UNATTESTED"
_R206_XML_MEMBERS = frozenset(
    relative for relative in NATIVE_EVIDENCE_PATHS if relative.endswith(".xml")
)
_R206_JSON_GENERATIONS = {
    "session/stress.json": 2,
    "catalog/full-catalog.json": 2,
    "cadence/cadence-fresh.json": 12,
    "cadence/cadence-legacy.json": 12,
}
_R206_MAX_CASES = 100000
_R206_STRESS_SCENARIOS = (
    "sql_shadow_wal",
    "publication_io_identity",
    "signals_risk_exit_ledger",
    "sources_readiness_network",
    "scalping_capacity",
    "treasury_caucion",
    "dashboard_auth_52",
    "scheduler_monitor_backup",
)



def _r206_public_junit_bytes(name: str, tests: int, private_size: int,
                            raw_sha256: str) -> bytes:
    """Literal ASCII projection; never serialize source attributes or text."""
    _require(name in _R206_XML_MEMBERS and type(tests) is int and
             0 < tests <= _R206_MAX_CASES and
             type(private_size) is int and 0 < private_size <= MAX_FILE_BYTES and
             type(raw_sha256) is str and _DIGEST.fullmatch(raw_sha256) is not None,
             "PRIVACY_PUBLIC_XML_SCHEMA")
    header = (
        '<testsuite schema="RC6_R206_PUBLIC_JUNIT_V1" '
        'status="PRIVATE_SOURCE_UNATTESTED" path="' + name +
        '" name="R206_PUBLIC_SYNTHETIC_ONLY" tests="' + str(tests) +
        '" failures="0" errors="0" skipped="0" private_raw_size_bytes="' +
        str(private_size) + '" private_raw_sha256_unattested="' +
        raw_sha256 + '" release_go="false">'
    )
    cases = "".join(
        '<testcase name="case-%06d"/>' % index
        for index in range(1, tests + 1)
    )
    projected = (header + cases + '</testsuite>\n').encode("ascii")
    _require(len(projected) <= MAX_FILE_BYTES, "PRIVACY_PUBLIC_XML_SCHEMA")
    return projected


def _r206_public_json_bytes(name: str, private_size: int,
                           raw_sha256: str) -> bytes:
    """All source fields are discarded; only fixed synthetic metadata survives."""
    _require(name in _R206_JSON_GENERATIONS and
             type(private_size) is int and 0 < private_size <= MAX_FILE_BYTES and
             type(raw_sha256) is str and _DIGEST.fullmatch(raw_sha256) is not None,
             "PRIVACY_PUBLIC_JSON_SCHEMA")
    record = {
        "schema": _R206_PUBLIC_JSON_SCHEMA,
        "status": _R206_PRIVATE_STATUS,
        "source_path": name,
        "private_raw_size_bytes": private_size,
        "private_raw_sha256_unattested": raw_sha256,
        "synthetic_fixture_only": True,
        "release_go": False,
        "real_orders_sent": 0,
        "catalog_count": 14611,
        "ready_fixture_count": 9902,
        "required_generations": _R206_JSON_GENERATIONS[name],
    }
    return (json.dumps(record, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def _r206_source_junit_count(raw: bytes) -> int:
    """Independently recount private testcases before revealing only ordinals."""
    _require(type(raw) is bytes and 0 < len(raw) <= MAX_FILE_BYTES and
             raw.count(b"<") <= _R206_MAX_CASES * 4 and
             b"\x00" not in raw, "PRIVACY_PRIVATE_JUNIT")
    upper = raw.upper()
    _require(all(marker not in upper for marker in
                 (b"<!DOCTYPE", b"<!ENTITY", b"<![CDATA")),
             "PRIVACY_PRIVATE_JUNIT")
    try:
        root = _etree.fromstring(raw)
    except (_etree.ParseError, ValueError, UnicodeError, TypeError,
            RecursionError) as exc:
        raise VolumeProofHold("PRIVACY_PRIVATE_JUNIT") from exc
    _require(root.tag in ("testsuite", "testsuites"), "PRIVACY_PRIVATE_JUNIT")
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    _require(0 < len(suites) <= _R206_MAX_CASES and
             len(suites) == len(list(root.iter("testsuite"))),
             "PRIVACY_PRIVATE_JUNIT")
    total = 0
    for suite in suites:
        values = {}
        for key in ("tests", "failures", "errors", "skipped"):
            literal = suite.get(key)
            _require(type(literal) is str and
                     re.fullmatch(r"[0-9]+", literal) is not None and
                     len(literal) <= 9, "PRIVACY_PRIVATE_JUNIT")
            values[key] = int(literal)
        direct = len(suite.findall("testcase"))
        _require(0 < values["tests"] <= _R206_MAX_CASES and
                 values["tests"] == direct and
                 not any(values[key] for key in ("failures", "errors", "skipped")),
                 "PRIVACY_PRIVATE_JUNIT")
        total += direct
        _require(total <= _R206_MAX_CASES, "PRIVACY_PRIVATE_JUNIT")
    _require(not any(next(root.iter(tag), None) is not None
                     for tag in ("failure", "error", "skipped")) and
             len(list(root.iter("testcase"))) == total,
             "PRIVACY_PRIVATE_JUNIT")
    return total


def _r206_assert_paper_zero(payload: dict, reason: str) -> None:
    _require(type(payload) is dict and
             type(payload.get("real_orders_sent")) is int and
             payload["real_orders_sent"] == 0 and
             type(payload.get("provider_requests")) is int and
             payload["provider_requests"] == 0 and
             payload.get("real_routes") == "NOT_CALLED",
             reason)


def _r206_assert_catalog_source(payload: dict, generations: int) -> None:
    reason = "PRIVACY_PRIVATE_JSON"
    _r206_assert_paper_zero(payload, reason)
    _require(payload.get("schema") == "rc6.synthetic-full-catalog.v1" and
             type(payload.get("catalog_count")) is int and
             payload["catalog_count"] == 14611 and
             type(payload.get("ready_fixture_count")) is int and
             payload["ready_fixture_count"] == 9902 and
             type(payload.get("required_generations")) is int and
             payload["required_generations"] == generations and
             payload.get("database_unchanged") is True and
             payload.get("production_data_mounted") is False and
             payload.get("actual_market_acceptance") is False and
             type(payload.get("cuts")) is list and
             len(payload["cuts"]) == generations,
             reason)


def _r206_parse_finite_float(literal: str) -> float:
    try:
        value = float(literal)
    except (ValueError, OverflowError) as exc:
        raise VolumeProofHold("PRIVACY_PRIVATE_JSON") from exc
    _require(math.isfinite(value), "PRIVACY_PRIVATE_JSON")
    return value


def _r206_assert_private_json(name: str, raw: bytes) -> None:
    reason = "PRIVACY_PRIVATE_JSON"
    _require(type(raw) is bytes and 0 < len(raw) <= MAX_FILE_BYTES and
             b"\x00" not in raw and name in _R206_JSON_GENERATIONS, reason)
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs_unique,
                             parse_constant=lambda _: _require(False, reason),
                             parse_float=_r206_parse_finite_float)
    except VolumeProofHold:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise VolumeProofHold(reason) from exc
    _require(type(payload) is dict, reason)
    if name == "session/stress.json":
        _require(payload.get("schema") == "rc6.postdeploy-stress.v1" and
                 payload.get("status") == "GREEN" and
                 payload.get("production_data_mounted") is False and
                 payload.get("ppi_watch_accessed") is False and
                 type(payload.get("scenarios")) is list and
                 len(payload["scenarios"]) == len(_R206_STRESS_SCENARIOS), reason)
        _r206_assert_paper_zero(payload.get("session"), reason)
        _r206_assert_catalog_source(payload.get("full_catalog"), 2)
        for index, scenario in enumerate(payload["scenarios"]):
            _require(type(scenario) is dict and
                     scenario.get("name") == _R206_STRESS_SCENARIOS[index] and
                     type(scenario.get("cases")) is int and
                     0 < scenario["cases"] <= _R206_MAX_CASES and
                     type(scenario.get("junit_sha256")) is str and
                     _DIGEST.fullmatch(scenario["junit_sha256"]) is not None and
                     all(type(scenario.get(key)) is int and scenario[key] == 0
                         for key in ("exit_code", "failure", "error", "skipped")),
                     reason)
    elif name == "catalog/full-catalog.json":
        _r206_assert_catalog_source(payload, 2)
        _require("status" not in payload, reason)
    else:
        _r206_assert_catalog_source(payload, 12)
        expected_mode = "fresh" if name.endswith("fresh.json") else "legacy"
        _require(payload.get("status") == "GREEN" and
                 payload.get("cadence_mode") == expected_mode and
                 type(payload.get("observed_publications")) is int and
                 payload["observed_publications"] == 12, reason)


def _project_private_native_member(name: str, raw: bytes) -> bytes:
    """One-way collector projection; SHA is unilateral, never raw attestation."""
    _require(name in NATIVE_EVIDENCE_PATHS and
             type(raw) is bytes and 0 < len(raw) <= MAX_FILE_BYTES,
             "PRIVACY_PRIVATE_MEMBER_INVALID")
    digest = hashlib.sha256(raw).hexdigest()
    if name in _R206_XML_MEMBERS:
        count = _r206_source_junit_count(raw)
        return _r206_public_junit_bytes(name, count, len(raw), digest)
    _r206_assert_private_json(name, raw)
    return _r206_public_json_bytes(name, len(raw), digest)


def _r206_verify_public_xml(name: str, content: bytes) -> None:
    reason = "PRIVACY_PUBLIC_XML_SCHEMA"
    _require(type(content) is bytes and 0 < len(content) <= MAX_FILE_BYTES and
             content.count(b"<") <= _R206_MAX_CASES + 3 and
             not any(marker in content.upper() for marker in
                     (b"<!DOCTYPE", b"<!ENTITY", b"<![CDATA")),
             reason)
    try:
        root = _etree.fromstring(content)
        _require(root.tag == "testsuite" and
                 set(root.attrib) == {
                     "schema", "status", "path", "name", "tests", "failures",
                     "errors", "skipped", "private_raw_size_bytes",
                     "private_raw_sha256_unattested", "release_go",
                 } and root.get("schema") == _R206_PUBLIC_XML_SCHEMA and
                 root.get("status") == _R206_PRIVATE_STATUS and
                 root.get("path") == name and
                 root.get("name") == "R206_PUBLIC_SYNTHETIC_ONLY" and
                 all(root.get(key) == "0" for key in
                     ("failures", "errors", "skipped")) and
                 root.get("release_go") == "false", reason)
        tests = root.get("tests")
        size = root.get("private_raw_size_bytes")
        _require(type(tests) is str and
                 re.fullmatch(r"[1-9][0-9]*", tests) is not None and
                 type(size) is str and
                 re.fullmatch(r"[1-9][0-9]*", size) is not None, reason)
        expected = _r206_public_junit_bytes(
            name, int(tests), int(size),
            root.get("private_raw_sha256_unattested"))
        _require(content == expected, reason)
    except VolumeProofHold as exc:
        raise VolumeProofHold(reason) from exc
    except (_etree.ParseError, ValueError, TypeError, UnicodeError,
            RecursionError) as exc:
        raise VolumeProofHold(reason) from exc


def _r206_verify_public_json(name: str, content: bytes) -> None:
    reason = "PRIVACY_PUBLIC_JSON_SCHEMA"
    _require(type(content) is bytes and 0 < len(content) <= 2048, reason)
    try:
        parsed = json.loads(content.decode("ascii"), object_pairs_hook=_pairs_unique)
        _require(type(parsed) is dict and
                 set(parsed) == {
                     "schema", "status", "source_path", "private_raw_size_bytes",
                     "private_raw_sha256_unattested", "synthetic_fixture_only",
                     "release_go", "real_orders_sent", "catalog_count",
                     "ready_fixture_count", "required_generations",
                 } and parsed.get("schema") == _R206_PUBLIC_JSON_SCHEMA and
                 parsed.get("status") == _R206_PRIVATE_STATUS and
                 parsed.get("source_path") == name and
                 parsed.get("synthetic_fixture_only") is True and
                 parsed.get("release_go") is False and
                 type(parsed.get("real_orders_sent")) is int and
                 parsed["real_orders_sent"] == 0 and
                 type(parsed.get("catalog_count")) is int and
                 parsed["catalog_count"] == 14611 and
                 type(parsed.get("ready_fixture_count")) is int and
                 parsed["ready_fixture_count"] == 9902 and
                 type(parsed.get("required_generations")) is int and
                 parsed["required_generations"] == _R206_JSON_GENERATIONS[name],
                 reason)
        expected = _r206_public_json_bytes(
            name, parsed.get("private_raw_size_bytes"),
            parsed.get("private_raw_sha256_unattested"))
        _require(content == expected, reason)
    except VolumeProofHold as exc:
        raise VolumeProofHold(reason) from exc
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise VolumeProofHold(reason) from exc


def _privacy_check_member(name: str, content: bytes) -> None:
    """Best-effort high-signal privacy policy, not an absence-of-secrets proof."""
    _require(b"\x00" not in content, "PRIVACY_PAYLOAD_BINARY")
    _require(_PRIVACY_SECRET_RE.search(content) is None,
             "PRIVACY_SECRET_SIGNATURE")
    if name in NATIVE_EVIDENCE_PATHS:
        # Public TAR admits ONLY the exact synthetic projection. The raw
        # collector source is held privately, regardless of known signatures.
        if name in _R206_XML_MEMBERS:
            _r206_verify_public_xml(name, content)
        else:
            _r206_verify_public_json(name, content)
        return
    try:
        content.decode("utf-8")  # one canonical text encoding only
        if name.endswith(".xml"):
            # Reject entity/DOCTYPE injection; JUnit must be synthetic XML.
            _require(b"<!DOCTYPE" not in content.upper() and
                     b"<!ENTITY" not in content.upper() and
                     b"<![CDATA[" not in content.upper(),
                     "PRIVACY_XML_META")
            root = _etree.fromstring(content)
            _require(root.tag in ("testsuite", "testsuites"),
                     "PRIVACY_XML_ROOT")
            for node in root.iter():
                for value in (node.text, node.tail, *node.attrib.values()):
                    if value is not None:
                        _require(_PRIVACY_SECRET_RE.search(
                            value.encode("utf-8")) is None,
                            "PRIVACY_XML_DECODED_SECRET")
        else:
            parsed = json.loads(content.decode("utf-8"),
                                object_pairs_hook=_pairs_unique,
                                parse_constant=lambda _: _require(
                                    False, "PRIVACY_JSON_NONFINITE"))
            _require(type(parsed) in (dict, list), "PRIVACY_JSON_ROOT")
            _privacy_json_tree(parsed, depth=0, budget=[50000])
    except VolumeProofHold:
        raise
    except (ValueError, UnicodeError, TypeError, RecursionError,
            _etree.ParseError) as exc:
        raise VolumeProofHold("PRIVACY_PAYLOAD_INVALID") from exc


def verify_native_tar_privacy(raw: bytes) -> list[str]:
    """Inspect all 13 ALLOWLIST payload bytes before any public artifact upload.

    This rejects known credential patterns and unknown malformed XML/JSON.
    It cannot prove absence of arbitrary opaque secrets: no automatic GO.
    """
    files = _native_tar_files(raw)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        for item in archive:
            relative = item.name[2:] if item.name.startswith("./") else item.name
            stream = archive.extractfile(item)
            _require(stream is not None, "PRIVACY_CONTENT_MISSING")
            _privacy_check_member(relative, stream.read(item.size + 1))
    return [row["path"] for row in files]


def verify_probe_zip_privacy(raw_zip: bytes, *,
                             source_cid: str, collector_cid: str,
                             candidate_sha: str, candidate_tree_sha: str,
                             image_id: str, run_id: str,
                             run_attempt: str, role: str) -> int:
    """Fail closed on a preupload IN-MEMORY ZIP preview, not actual upload bytes.

    The real GitHub-generated ZIP still requires independent trusted-main
    byte verification; this guard checks only the six prospective members.
    """
    _require(type(raw_zip) is bytes and
             0 < len(raw_zip) <= 64 * 1024 * 1024, "PRIVACY_ZIP_BYTES")
    expected = sorted((
        "source-inspect.json", "collector-inspect.json", "custody.json",
        "native.tar", "native-manifest.json",
        "porota-artifact-byte-manifest-v1.json",
    ))
    try:
        with _zipfile.ZipFile(io.BytesIO(raw_zip)) as package:
            info = package.infolist()
            _require(len(info) == 6 and
                     sorted(row.filename for row in info) == expected and
                     all(not row.is_dir() and
                         row.compress_type in (_zipfile.ZIP_STORED,
                                               _zipfile.ZIP_DEFLATED) and
                         row.flag_bits & 0x01 == 0 and
                         0 < row.file_size <= (
                             MAX_TAR_BYTES if row.filename == "native.tar"
                             else 262144) and
                         0 < row.compress_size and
                         row.file_size <= row.compress_size * 200
                         for row in info), "PRIVACY_ZIP_ALLOWLIST")
            payloads = {row.filename: package.read(row) for row in info}
    except (_zipfile.BadZipFile, OSError, ValueError, OverflowError) as exc:
        if isinstance(exc, VolumeProofHold):
            raise
        raise VolumeProofHold("PRIVACY_ZIP_PARSE") from exc
    # Inspect known-pattern secrets in every raw ZIP member before parsing.
    # Native TAR is also traversed member by member below.
    for value in payloads.values():
        _require(_PRIVACY_SECRET_RE.search(value) is None,
                 "PRIVACY_ZIP_SECRET_SIGNATURE")
    identity = {
        "source_cid": source_cid, "collector_cid": collector_cid,
        "candidate_sha": candidate_sha, "candidate_tree_sha": candidate_tree_sha,
        "image_id": image_id, "run_id": run_id,
        "run_attempt": run_attempt, "role": role,
    }
    custody = verify_public_inspects_v2(
        producer_public_json=payloads["source-inspect.json"],
        collector_public_json=payloads["collector-inspect.json"],
        **identity,
    )
    _require(_strict_equal(custody, _json_bytes(payloads["custody.json"])),
             "PRIVACY_CUSTODY_MISMATCH")
    for name in ("custody.json", "native-manifest.json",
                 "porota-artifact-byte-manifest-v1.json"):
        _privacy_check_member(name, payloads[name])
    verified = verify_collector_manifest(
        payloads["native.tar"], payloads["native-manifest.json"],
        expected_candidate_sha=candidate_sha,
        expected_candidate_tree_sha=candidate_tree_sha,
        expected_run_id=run_id,
        expected_run_attempt=run_attempt,
        expected_source_cid=source_cid,
        expected_collector_cid=collector_cid,
        expected_volume_name=custody["volume_name"],
        expected_image_id=image_id, expected_role=role,
    )
    _require(verified["host_or_runtime_verified"] is False,
             "PRIVACY_NATIVE_STATUS")
    paths = verify_native_tar_privacy(payloads["native.tar"])
    artifact_manifest = _json_bytes(payloads[
        "porota-artifact-byte-manifest-v1.json"])
    expected_entries = sorted(x for x in expected if x !=
                              "porota-artifact-byte-manifest-v1.json")
    _require(type(artifact_manifest) is dict and
             artifact_manifest.get("schema_version") == 1 and
             artifact_manifest.get("candidate_sha") == candidate_sha and
             artifact_manifest.get("candidate_tree_sha") == candidate_tree_sha and
             artifact_manifest.get("run_id") == int(run_id) and
             artifact_manifest.get("run_attempt") == int(run_attempt) and
             artifact_manifest.get("file_count") == 5 and
             type(artifact_manifest.get("files")) is list and
             [row.get("path") for row in artifact_manifest["files"]
              if type(row) is dict] == expected_entries,
             "PRIVACY_ZIP_MANIFEST")
    rows = artifact_manifest["files"]
    _require(len(rows) == 5 and
             all(type(row) is dict and set(row) == {
                 "path", "type", "size_bytes", "sha256",
             } and row["type"] == "file" and
                 type(row["path"]) is str and
                 row["path"] in payloads and
                 type(row["size_bytes"]) is int and
                 row["size_bytes"] == len(payloads[row["path"]]) and
                 type(row["sha256"]) is str and
                 row["sha256"] == hashlib.sha256(
                     payloads[row["path"]]).hexdigest()
                 for row in rows) and
             artifact_manifest.get("total_size_bytes") ==
             sum(row["size_bytes"] for row in rows),
             "PRIVACY_ZIP_DIGESTS")
    return len(paths)


def challenge_privacy_patterns(raw_tar: bytes,
                               producer_public_json: bytes,
                               collector_public_json: bytes) -> None:
    """Exercise each TAR13 path and both public docs using a random FAKE canary.

    The marker lives only in volatile memory. It is never emitted or uploaded;
    failing to recognize it is a preupload HOLD79, not a compromised credential.
    """
    marker = b"CANARY_SECRET_" + _secrets.token_hex(16).encode("ascii")
    _require(type(producer_public_json) is bytes and
             type(collector_public_json) is bytes and
             _PRIVACY_SECRET_RE.search(producer_public_json) is None and
             _PRIVACY_SECRET_RE.search(collector_public_json) is None,
             "PRIVACY_PUBLIC_JSON_MARKER")
    for path in NATIVE_EVIDENCE_PATHS:
        try:
            _privacy_check_member(path, marker)
        except VolumeProofHold as exc:
            _require(exc.reason == "PRIVACY_SECRET_SIGNATURE",
                     "PRIVACY_CANARY_BYPASS")
        else:
            _require(False, "PRIVACY_CANARY_BYPASS")
    for public_json in (producer_public_json, collector_public_json):
        _require(_PRIVACY_SECRET_RE.search(public_json + marker) is not None,
                 "PRIVACY_PUBLIC_CANARY_BYPASS")
    # A valid TAR must be clean before the adversarial challenge counts.
    verify_native_tar_privacy(raw_tar)



def _create_exclusive_private_output(path: str, data: bytes) -> None:
    """Write one public projection as a new, non-followed 0600 regular file."""
    _require(type(path) is str and path != "" and
             type(data) is bytes and 0 < len(data) <= 262144,
             "PUBLIC_OUTPUT_INVALID")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            fd = -1
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
    finally:
        if fd >= 0:
            os.close(fd)



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
                private = _safe_read_proof(root_fd, relative)
                contents = _project_private_native_member(relative, private)
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
    inspect = commands.add_parser("inspect", help="PRIVATE Docker inspect -> custody")
    project = commands.add_parser(
        "project", help="verified PRIVATE Docker inspect -> two PUBLIC V2 files")
    for command in (inspect, project):
        for option in ("producer-inspect", "collector-inspect", "source-cid",
                       "collector-cid", "candidate-sha", "candidate-tree-sha",
                       "image-id", "run-id", "run-attempt", "role"):
            command.add_argument("--" + option, required=True)
    project.add_argument("--producer-public", required=True)
    project.add_argument("--collector-public", required=True)
    privacy = commands.add_parser("privacy", help="TAR13 payload privacy HOLD79")
    privacy.add_argument("--tar", required=True)
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
        elif args.command == "project":
            public_producer, public_collector = project_public_inspects_v2(
                producer_inspect_json=_read_bounded(args.producer_inspect, 262144),
                collector_inspect_json=_read_bounded(args.collector_inspect, 262144),
                source_cid=args.source_cid, collector_cid=args.collector_cid,
                candidate_sha=args.candidate_sha,
                candidate_tree_sha=args.candidate_tree_sha,
                image_id=args.image_id, run_id=args.run_id,
                run_attempt=args.run_attempt, role=args.role)
            _require(args.producer_public != args.collector_public,
                     "PUBLIC_OUTPUT_PATHS_IDENTICAL")
            _create_exclusive_private_output(args.producer_public, public_producer)
            _create_exclusive_private_output(args.collector_public, public_collector)
            result = b""
        elif args.command == "privacy":
            verify_native_tar_privacy(_read_bounded(args.tar, MAX_TAR_BYTES))
            result = b""
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
