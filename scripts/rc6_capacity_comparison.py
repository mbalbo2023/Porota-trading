"""Authenticate capacity V2 evidence and recompute the costs we can prove.

This is a read-only verifier, not a quota implementation or launch authority.
The current RC6 bootstrap still has unbounded build/fetch/temporary writers.
In particular an observed retained sample, checks=True, a final wheel size, or
a historical quota readback does not prove their simultaneous physical peak.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
from typing import Callable
import urllib.parse
import urllib.request
import zipfile

REPOSITORY = "mbalbo2023/Porota-trading"
GRAPH_SCHEMA = "porota.rc6.capacity-cost-graph.v1"
MAX_INLINE_BYTES = 128 * 1024
MAX_CONTAINER_BYTES = 128 * 1024**2
MAX_TOTAL_BYTES = 256 * 1024**2
MAX_RECORDS = 256
UNIT = 4096
RESERVE = 4 * 1024**3
CANONICAL_PRODUCERS = frozenset({"bootstrap", "cheap", "focal311", "focal312", "BIG-browser",
                               "Horizon", "full-gov311", "full-gov312", "predeploy"})
BOOTSTRAP_COSTS = frozenset({
    "native-quota-backing", "native-quota-metadata",
    "cpython-tool-cache.311", "cpython-tool-cache.312",
    "wheel-downloads.311", "wheel-downloads.312", "installed-157.311", "installed-157.312",
    "full-git-fetch", "original19-git-fetch", "full-git-fsck", "full-source.311", "full-source.312",
    "directories", "controls", "logs", "temporary", "recovery",
    *("sdist-build." + package + "." + epoch
      for package in ("msgpack", "ppi-client", "signalrcoreppi", "ta") for epoch in ("311", "312")),
})
PRODUCER_COSTS = frozenset({"full-source", "full-git-clone", "source-export", "source-pins",
    "fixture-payloads", "fixture-clones", "directories", "controls", "FIN-controls", "logs",
    "captured-RAW", "artifact-custody", "temporary", "recovery"})
PREDEPLOY_COSTS = frozenset({"docker-build-context", "docker-daemon-build-layers",
                           "artifact-export", "artifact-tests", "artifact-security-controls"})
DYNAMIC_COSTS = frozenset({"cpython-tool-cache.311", "cpython-tool-cache.312",
    "native-quota-backing", "native-quota-metadata",
    "wheel-downloads.311", "wheel-downloads.312", "installed-157.311", "installed-157.312",
    "full-git-fetch", "original19-git-fetch", "full-git-fsck", "fixture-payloads", "fixture-clones",
    "directories", "controls", "logs", "temporary", "recovery", "browser", "archive", "restore", "gc",
    "full-git-clone", "source-export", "source-pins", "FIN-controls", "captured-RAW", "artifact-custody",
    *PREDEPLOY_COSTS,
    *("sdist-build." + package + "." + epoch
      for package in ("msgpack", "ppi-client", "signalrcoreppi", "ta") for epoch in ("311", "312")),
})


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def document(raw):
    def pairs(rows):
        value = {}
        for key, item in rows:
            require(key not in value, "CAPACITY_DUPLICATE_JSON_KEY")
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: require(False, "CAPACITY_NONFINITE_JSON"))


def _hash(value, length=64):
    require(type(value) is str and re.fullmatch("[0-9a-f]{" + str(length) + "}", value),
            "CAPACITY_EXACT_DIGEST_REQUIRED")
    return value


def _path(value):
    require(type(value) is str and 0 < len(value) <= 4096 and not value.startswith("/")
            and "\\" not in value and all(part not in ("", ".", "..") for part in value.split("/"))
            and not any(ord(char) < 32 or ord(char) == 127 for char in value),
            "CAPACITY_UNSAFE_EVIDENCE_PATH")
    return value


def _integer(value, *, zero=True):
    require(type(value) is int and (0 if zero else 1) <= value <= 2**63 - 1,
            "CAPACITY_INTEGER_BOUND_REQUIRED")
    return value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise ValueError("CAPACITY_AUTHENTICATED_API_REDIRECT_FORBIDDEN")


def authenticated_get(path, *, maximum_bytes=MAX_CONTAINER_BYTES * 4 // 3 + 65536):
    """GET only the fixed repository API; never expose or forward credentials."""
    require(type(path) is str and re.fullmatch(r"/git/(?:commits|trees|blobs)/[0-9a-f]{40}", path),
            "CAPACITY_AUTHENTICATED_API_SCOPE_REQUIRED")
    require(type(maximum_bytes) is int and 0 < maximum_bytes <= MAX_CONTAINER_BYTES * 4 // 3 + 65536,
            "CAPACITY_AUTHENTICATED_API_RESPONSE_BOUND")
    if "/git/commits/" in path:
        maximum_bytes = min(maximum_bytes, 1024**2)
    elif "/git/trees/" in path:
        maximum_bytes = min(maximum_bytes, 16 * 1024**2)
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    require(type(token) is str and bool(token), "CAPACITY_AUTHENTICATED_READ_TOKEN_REQUIRED")
    url = "https://api.github.com/repos/" + REPOSITORY + path
    request = urllib.request.Request(url, headers={"Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.build_opener(_NoRedirect).open(request, timeout=30) as response:
        require(response.status == 200 and response.geturl() == url, "CAPACITY_AUTHENTICATED_API_ORIGIN_REBOUND")
        raw = response.read(maximum_bytes + 1)
    require(len(raw) <= maximum_bytes, "CAPACITY_AUTHENTICATED_API_RESPONSE_BOUND")
    return document(raw)


class GitEvidenceReader:
    """Bounded immutable commit/tree/blob reads, with independent Git hashes.

    get is an injectable authenticated JSON transport, not an inline-evidence
    fallback. No network mutation, fetch, extraction, or lazy Git fetch occurs.
    """
    def __init__(self, get: Callable | None = None):
        self.get = get or authenticated_get
        self.cache = {}
        self.decoded_bytes = 0
        self.requests = 0

    def _get(self, path):
        self.requests += 1
        require(self.requests <= 2048, "CAPACITY_ORIGINAL_GIT_READ_COUNT_BOUND")
        value = self.get(path)
        require(type(value) is dict, "CAPACITY_AUTHENTICATED_GIT_DOCUMENT_REQUIRED")
        if "/git/trees/" in path:
            require(len(canonical(value)) <= 16 * 1024**2, "CAPACITY_ORIGINAL_GIT_TREE_RESPONSE_BOUND")
        elif "/git/commits/" in path:
            require(len(canonical(value)) <= 1024**2, "CAPACITY_ORIGINAL_GIT_COMMIT_RESPONSE_BOUND")
        return value

    def _tree(self, oid):
        key = ("tree", oid)
        if key not in self.cache:
            value = self._get("/git/trees/" + oid)
            rows = value.get("tree")
            require(value.get("sha") == oid and value.get("truncated") is False
                    and type(rows) is list and len(rows) <= 100000,
                    "CAPACITY_ORIGINAL_GIT_TREE_PARTIAL_OR_REBOUND")
            names, body = set(), bytearray()
            entries = []
            for row in rows:
                require(type(row) is dict, "CAPACITY_ORIGINAL_GIT_TREE_ROW_REQUIRED")
                name = _path(row.get("path"))
                require("/" not in name and name not in names, "CAPACITY_ORIGINAL_GIT_TREE_MEMBER_DUPLICATED")
                names.add(name)
                mode, kind = row.get("mode"), row.get("type")
                require((mode, kind) in (("040000", "tree"), ("100644", "blob"), ("100755", "blob"),
                    ("120000", "blob"), ("160000", "commit")), "CAPACITY_ORIGINAL_GIT_TREE_MODE_INVALID")
                entries.append((name, mode, kind, _hash(row.get("sha"), 40)))
            for name, mode, kind, member_oid in sorted(entries, key=lambda x: (x[0] + ("/" if x[2] == "tree" else "")).encode()):
                body.extend(mode.lstrip("0").encode() + b" " + name.encode() + b"\0" + bytes.fromhex(member_oid))
            header = b"tree " + str(len(body)).encode() + b"\0"
            require(hashlib.sha1(header + body).hexdigest() == oid, "CAPACITY_ORIGINAL_GIT_TREE_HASH_MISMATCH")
            self.cache[key] = {name: (mode, kind, member_oid) for name, mode, kind, member_oid in entries}
        return self.cache[key]

    def commit_tree(self, sha):
        _hash(sha, 40)
        key = ("commit", sha)
        if key not in self.cache:
            commit = self._get("/git/commits/" + sha)
            require(commit.get("sha") == sha and type(commit.get("tree")) is dict,
                    "CAPACITY_ORIGINAL_GIT_COMMIT_REBOUND")
            self.cache[key] = _hash(commit["tree"].get("sha"), 40)
        return self.cache[key]

    def read(self, uri, *, maximum_bytes=MAX_CONTAINER_BYTES):
        require(type(uri) is str and type(maximum_bytes) is int and 0 <= maximum_bytes <= MAX_CONTAINER_BYTES,
                "CAPACITY_ORIGINAL_IMMUTABLE_REPOSITORY_EVIDENCE_REQUIRED")
        match = re.fullmatch(r"https://github\.com/mbalbo2023/Porota-trading/blob/([0-9a-f]{40})/([^?#\r\n]+)", uri)
        require(match is not None and len(uri) <= 8192, "CAPACITY_ORIGINAL_IMMUTABLE_REPOSITORY_EVIDENCE_REQUIRED")
        sha, encoded = match.groups()
        path = _path(urllib.parse.unquote(encoded, encoding="utf-8", errors="strict"))
        oid = self.commit_tree(sha)
        parts = path.split("/")
        for number, name in enumerate(parts):
            rows = self._tree(oid)
            require(name in rows, "CAPACITY_ORIGINAL_GIT_MEMBER_MISSING")
            mode, kind, oid = rows[name]
            last = number == len(parts) - 1
            require((not last and kind == "tree") or (last and kind == "blob" and mode in ("100644", "100755")),
                    "CAPACITY_ORIGINAL_GIT_ALIAS_OR_NONREGULAR_MEMBER")
        key = ("blob", oid)
        if key not in self.cache:
            blob = self._get("/git/blobs/" + oid)
            size = _integer(blob.get("size"))
            require(blob.get("sha") == oid and blob.get("encoding") == "base64" and type(blob.get("content")) is str
                    and size <= maximum_bytes and len(blob["content"]) <= 2 * maximum_bytes + 1024,
                    "CAPACITY_ORIGINAL_GIT_BLOB_BOUND_OR_IDENTITY")
            encoded_raw = blob["content"].replace("\n", "")
            try:
                raw = base64.b64decode(encoded_raw, validate=True)
            except (ValueError, TypeError) as error:
                raise ValueError("CAPACITY_ORIGINAL_GIT_BLOB_BASE64_INVALID") from error
            require(len(raw) == size and hashlib.sha1(b"blob " + str(size).encode() + b"\0" + raw).hexdigest() == oid,
                    "CAPACITY_ORIGINAL_GIT_BLOB_BYTES_REBOUND")
            self.decoded_bytes += size
            require(self.decoded_bytes <= MAX_TOTAL_BYTES, "CAPACITY_ORIGINAL_GIT_TOTAL_READ_BOUND")
            self.cache[key] = raw
        require(len(self.cache[key]) <= maximum_bytes, "CAPACITY_ORIGINAL_GIT_BLOB_BOUND_OR_IDENTITY")
        return self.cache[key]


def _load_ref(reader, value, *, maximum_bytes=MAX_INLINE_BYTES):
    require(type(value) is dict, "CAPACITY_ORIGINAL_RAW_REFERENCE_REQUIRED")
    expected = _hash(value.get("sha256"))
    raw = reader.read(value.get("uri"), maximum_bytes=maximum_bytes)
    require(digest(raw) == expected, "CAPACITY_AUTHENTICATED_ORIGINAL_RAW_DIGEST_MISMATCH")
    return raw


def authenticate_records(manifest, reader):
    records = manifest.get("records")
    require(type(records) is list and 0 < len(records) <= MAX_RECORDS, "CAPACITY_HASH_ONLY_COMPARISON_EVIDENCE_BLOCKED")
    found, total = {}, 0
    for record in records:
        require(type(record) is dict and record.get("uri") not in found, "CAPACITY_EVIDENCE_DUPLICATED_OR_DIGEST_MISSING")
        require(("raw_utf8" in record) != ("raw_base64" in record), "CAPACITY_ORIGINAL_RAW_BYTES_REQUIRED")
        if "raw_utf8" in record:
            require(type(record["raw_utf8"]) is str and len(record["raw_utf8"]) <= MAX_INLINE_BYTES,
                    "CAPACITY_ORIGINAL_RAW_UTF8_REQUIRED")
            raw = record["raw_utf8"].encode()
        else:
            require(type(record["raw_base64"]) is str and len(record["raw_base64"]) <= 2 * MAX_INLINE_BYTES,
                    "CAPACITY_ORIGINAL_RAW_BASE64_REQUIRED")
            raw = base64.b64decode(record["raw_base64"], validate=True)
        total += len(raw)
        require(0 < len(raw) and total <= MAX_INLINE_BYTES, "CAPACITY_COMPARISON_RAW_BOUND")
        require(digest(raw) == _hash(record.get("sha256")), "CAPACITY_ORIGINAL_RAW_DIGEST_MISMATCH")
        require(_load_ref(reader, record) == raw, "CAPACITY_INLINE_BYTES_DO_NOT_MATCH_AUTHENTIC_ORIGINAL")
        found[record["uri"]] = raw
    return found


def original_measurement(reference, reader):
    """Verify the existing lossless pack, its full index, CRC and selected RAW."""
    from scripts.rc6_material_carrier import verify_diagnostic_pack
    evidence = reference.get("evidence")
    require(type(evidence) is dict, "CAPACITY_ORIGINAL_MEASUREMENT_PROVENANCE_REQUIRED")
    container_ref = {"uri": evidence.get("uri"), "sha256": evidence.get("container_sha256")}
    container = _load_ref(reader, container_ref, maximum_bytes=MAX_CONTAINER_BYTES)
    index_ref = evidence.get("index")
    index_raw = _load_ref(reader, index_ref, maximum_bytes=64 * 1024**2)
    # Container and index must be original siblings at the same immutable cut.
    require(container_ref["uri"].split("/blob/", 1)[1].split("/", 1)[0]
            == index_ref["uri"].split("/blob/", 1)[1].split("/", 1)[0],
            "CAPACITY_ORIGINAL_CONTAINER_INDEX_COMMIT_REBOUND")
    index = document(index_raw)
    require(type(index.get("binding")) is dict and index["binding"].get("candidate_sha") == reference.get("source_sha")
            and index["binding"].get("candidate_tree") == reference.get("source_tree"),
            "CAPACITY_ORIGINAL_INDEX_SOURCE_REBOUND")
    try:
        verify_diagnostic_pack(container, index)
        member = _path(evidence.get("raw_member"))
        with zipfile.ZipFile(io.BytesIO(container)) as packed:
            info = packed.getinfo(member)
            require(info.file_size <= MAX_INLINE_BYTES, "CAPACITY_ORIGINAL_MEASUREMENT_MEMBER_BOUND")
            raw = packed.read(info)
    except (KeyError, zipfile.BadZipFile) as error:
        raise ValueError("CAPACITY_ORIGINAL_CONTAINER_MEMBER_OR_CRC_INVALID") from error
    require(digest(raw) == _hash(evidence.get("raw_member_sha256")), "CAPACITY_ORIGINAL_MEASUREMENT_MEMBER_DIGEST_MISMATCH")
    value = document(raw)
    path = evidence.get("measurement_path", [])
    require(type(path) is list and len(path) <= 16 and all(type(part) is str and part for part in path),
            "CAPACITY_ORIGINAL_MEASUREMENT_SELECTOR_INVALID")
    for part in path:
        require(type(value) is dict and part in value, "CAPACITY_ORIGINAL_MEASUREMENT_SELECTOR_MISSING")
        value = value[part]
    require(type(value) is dict and value == evidence.get("measurement")
            and digest(canonical(value)) == evidence.get("measurement_sha256"),
            "CAPACITY_ORIGINAL_MEASUREMENT_BYTES_OR_METRIC_REBOUND")
    require(value.get("allocated_bytes") == reference.get("allocated_bytes")
            and value.get("retained_entries") == reference.get("retained_entries")
            and value.get("origin_runner_class") == reference.get("runner_class")
            and value.get("scope") == reference.get("measurement_scope"),
            "CAPACITY_ORIGINAL_MEASUREMENT_ORIGIN_OR_COUNT_REBOUND")
    _integer(value.get("allocated_bytes"), zero=False)
    _integer(value.get("retained_entries"))
    return value


def source_delta(reference, target):
    """Exact whole-Source delta: bytes, modes and Git identities all matter."""
    left = {row["path"]: row for row in reference["files"]}
    right = {row["path"]: row for row in target["files"]}
    return {"reference_source_manifest_sha256": digest(canonical(reference)),
        "target_source_manifest_sha256": digest(canonical(target)),
        "added": [right[name] for name in sorted(right.keys() - left.keys())],
        "removed": [left[name] for name in sorted(left.keys() - right.keys())],
        "changed": [{"before": left[name], "after": right[name]} for name in sorted(left.keys() & right.keys())
                    if left[name] != right[name]]}


def required_costs(producer):
    costs = BOOTSTRAP_COSTS if producer == "bootstrap" else PRODUCER_COSTS
    if producer == "predeploy":
        costs = costs | BOOTSTRAP_COSTS | PREDEPLOY_COSTS
    if producer == "BIG-browser":
        costs = costs | {"browser"}
    if producer == "Horizon":
        costs = costs | {"archive", "restore", "gc", "browser"}
    return costs


def evaluate_bootstrap_storage(*, free_bytes, nominal_storage_bytes, total_inodes,
                               free_inodes, allocation_unit_bytes):
    """Necessary storage floor from the actual bootstrap code, never a PASS.

    This pure arithmetic guard starts no actor, loop, mount, quota or fetch.
    It does not establish the unknown compound bootstrap peak, filesystem
    support, platform availability, writer confinement or ROOT signal custody.
    A concrete runner's extra observed space cannot upgrade the 14 GiB promise.
    """
    from scripts.rc6_capacity_calibration import limits_for, outer_control_peak_bound
    _integer(free_bytes)
    _integer(nominal_storage_bytes, zero=False)
    _integer(total_inodes, zero=False)
    _integer(free_inodes)
    require(free_inodes <= total_inodes, "CAPACITY_INODE_MEASUREMENT_INVALID")
    controls = outer_control_peak_bound(allocation_unit_bytes)
    limits = limits_for("bootstrap")
    require(limits == {"backing_image_bytes": 26 * 1024**3, "project_hard_limit_bytes": 20 * 1024**3,
                       "residual_reserve_bytes": RESERVE}, "CAPACITY_ORIGINAL_BOOTSTRAP_LIMITS_CHANGED")
    required = limits["backing_image_bytes"] + limits["residual_reserve_bytes"] + controls["total_bytes"]
    blockers = []
    if free_bytes < required:
        blockers.append("CAPACITY_BOOTSTRAP_BACKING_RESERVE_CONTROLS_INSUFFICIENT")
    if free_inodes * 10 < total_inodes:
        blockers.append("CAPACITY_INODES_INSUFFICIENT")
    return {"schema": "porota.rc6.bootstrap-storage-necessary-floor.v1",
        "status": "BLOCKED_NECESSARY_STORAGE" if blockers else "NECESSARY_STORAGE_ONLY",
        "blockers": blockers, **limits, "outer_control_bound": controls,
        "required_minimum_bytes": required, "measured_free_bytes": free_bytes,
        "nominal_storage_bytes": nominal_storage_bytes,
        "nominal_storage_guarantees_necessary_floor": nominal_storage_bytes >= required,
        "minimum_free_inode_ratio": 0.1, "full_compound_peak_proved": False,
        "native_quota_or_privileged_custody_proved": False, "G0_GREEN_claimed": False,
        "launch_authorized": False, "live_filesystem_recheck_required": True}


def readonly_runner_observation(path):
    """Observe live runner metadata without any quota or privileged exercise.

    kernel_prerequisites only reads existing configuration, registrations,
    module metadata and system code; it never loads a kernel module or invokes
    the tools it finds. An unavailable observer is NO_VERIFICADO, not evidence
    that the kernel or all GitHub-hosted runners are incompatible.
    """
    from scripts import rc6_capacity_calibration as calibration
    from scripts import rc6_heavy_test_preflight as preflight
    started = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    selected = Path(path).absolute()
    errors = []
    def observation_error(component, error):
        value = {"component": component, "status": "NO_VERIFICADO",
                 "error_type": type(error).__name__, "errno": getattr(error, "errno", None),
                 "reason": str(error)[:2048]}
        errors.append(value)
        return value
    filesystem, live_filesystem, floor = None, None, None
    try:
        filesystem = calibration.filesystem(selected)
        live_filesystem = preflight.measure_filesystem(selected)
        require(filesystem["device"] == live_filesystem["filesystem_device"]
                and filesystem["mount_id"] == live_filesystem["mount_id"]
                and filesystem["fragment_bytes"] == live_filesystem["allocation_unit_bytes"],
                "CAPACITY_READONLY_FILESYSTEM_REBOUND_DURING_OBSERVATION")
        floor = evaluate_bootstrap_storage(
            free_bytes=min(filesystem["available_bytes"], live_filesystem["free_bytes"]),
            nominal_storage_bytes=14 * 1024**3,
            total_inodes=filesystem["total_inodes"],
            free_inodes=min(filesystem["free_inodes"], live_filesystem["free_inodes"]),
            allocation_unit_bytes=filesystem["fragment_bytes"])
    except Exception as error:
        observation_error("filesystem_and_storage", error)
    try:
        kernel = calibration.kernel_prerequisites("bootstrap")
        require(type(kernel) is dict and kernel.get("schema") == "porota.rc6.readonly-kernel-quota-prerequisites.v1"
                and kernel.get("status") in ("NO_VERIFICADO", "BLOQUEADO", "PREREQUISITES_PRESENT")
                and all(kernel.get(key) is False for key in ("actual_capability_proved", "module_loading_attempted",
                    "qualification_claimed", "kernel_unsupported_claimed")),
                "CAPACITY_METADATA_OBSERVER_CANNOT_CLAIM_NATIVE_QUOTA_OR_GLOBAL_INCOMPATIBILITY")
    except Exception as error:
        kernel = {"schema": "porota.rc6.readonly-kernel-quota-prerequisites.v1", "status": "NO_VERIFICADO",
            "actual_capability_proved": False, "module_loading_attempted": False,
            "qualification_claimed": False, "kernel_unsupported_claimed": False,
            "observer_error": observation_error("kernel_prerequisites", error)}
    try:
        machine = preflight.machine_snapshot()
    except Exception as error:
        machine = {"status": "NO_VERIFICADO", "observer_error": observation_error("machine", error)}
    return {"schema": "porota.rc6.readonly-runner-observation.v1",
        "scope": "READ_ONLY_METADATA_AND_NECESSARY_STORAGE_NO_G0_QUALIFICATION",
        "status": "READ_ONLY_OBSERVATIONS_WITH_UNKNOWNS" if errors else "READ_ONLY_OBSERVATIONS_RECORDED",
        "path": str(selected), "observed_started_utc": started,
        "observed_completed_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "filesystem": filesystem, "live_filesystem": live_filesystem, "storage_floor": floor,
        "kernel_prerequisites": kernel, "machine": machine,
        "privileged_signal_custody": dict(calibration.PRIVILEGED_SIGNAL_CUSTODY),
        "observer_errors": errors,
        "generation_operations": {"image_allocation": "NOT_CALLED", "root_worker_launch": "NOT_CALLED",
            "namespace_creation": "NOT_CALLED", "mount": "NOT_CALLED", "quota_exercise": "NOT_CALLED",
            "module_loading": "NOT_CALLED"},
        "G0_status": "BLOQUEADO", "G0_GREEN_claimed": False, "launch_authorized": False,
        "actual_capability_proved": False, "compound_bootstrap_peak_proved": False,
        "platform_compatibility_proved": False, "kernel_unsupported_claimed": False,
        "runtime_validated": False, "live_filesystem_recheck_required": True}


def recompute_costs(graph, inventory, *, reader):
    """No declared arithmetic or verification flag substitutes for a bound.

    EXACT_FROZEN_SOURCE_REGULAR_PAYLOAD accounts only regular payload blocks.
    Metadata, directories, transient clones, packs, recovery and writers are
    separate mandatory costs. In particular this leaf is not a fullSource or
    bootstrap peak certificate. Dynamic RC6 writers currently have no accepted
    proof model; historical quota metadata cannot grant a live confinement proof.
    """
    require(type(graph) is dict and graph.get("schema") == GRAPH_SCHEMA
            and graph.get("source_sha") == inventory["source_sha"] and graph.get("source_tree") == inventory["source_tree"],
            "CAPACITY_COST_GRAPH_SOURCE_OR_SCHEMA_REBOUND")
    producer = graph.get("producer")
    require(type(producer) is str and producer, "CAPACITY_COST_GRAPH_PRODUCER_REQUIRED")
    require(graph.get("unknown_components") == [] and type(graph.get("unknown_components")) is list,
            "CAPACITY_COMPARISON_UNKNOWN_COMPONENTS_BEFORE_BOOTSTRAP")
    require(type(graph.get("premature_cleanup_credit_bytes")) is int and graph["premature_cleanup_credit_bytes"] == 0,
            "CAPACITY_PREMATURE_CLEANUP_CREDIT_BLOCKED")
    model = graph.get("allocation_model")
    require(model == {"filesystem_type": "ext4", "allocation_unit_bytes": UNIT,
                      "metric": "REGULAR_PAYLOAD_CEIL4096_PLUS_SEPARATELY_BOUNDED_COSTS"},
            "CAPACITY_COST_ALLOCATION_MODEL_UNSUPPORTED")
    nodes = graph.get("nodes")
    require(type(nodes) is list and 0 < len(nodes) <= 256, "CAPACITY_COMPLETE_COST_GRAPH_REQUIRED")
    names = [node.get("name") if type(node) is dict else None for node in nodes]
    require(all(type(name) is str and re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", name) for name in names)
            and len(names) == len(set(names)), "CAPACITY_GRAPH_COMPONENT_DUPLICATED_OR_INVALID")
    if producer in CANONICAL_PRODUCERS:
        require(set(names) == required_costs(producer), "CAPACITY_BOOTSTRAP_OR_PRODUCER_COST_GRAPH_INCOMPLETE")
        require(graph.get("scope") == "ORIGINAL_RC6_STAGE_COMPLETE", "CAPACITY_DIAGNOSTIC_GRAPH_CANNOT_QUALIFY_RC6")
    else:
        require(graph.get("scope") == "DIAGNOSTIC_IMMUTABLE_PAYLOADS_ONLY",
                "CAPACITY_NONCANONICAL_GRAPH_SCOPE_REQUIRED")
    costs, unsupported = {}, []
    for node in nodes:
        name, kind = node["name"], node.get("model")
        quantity = _integer(node.get("quantity"), zero=False)
        if name in DYNAMIC_COSTS and producer in CANONICAL_PRODUCERS:
            unsupported.append(name)
            continue
        if kind == "EXACT_FROZEN_SOURCE_REGULAR_PAYLOAD":
            require(node.get("source_manifest_sha256") == digest(canonical(inventory)),
                    "CAPACITY_COMPONENT_SOURCE_MANIFEST_REBOUND")
            if producer in CANONICAL_PRODUCERS:
                require(quantity == 1 and name in ("full-source", "full-source.311", "full-source.312"),
                        "CAPACITY_ORIGINAL_FULLSOURCE_CLONE_COUNT_REBOUND")
            amount = sum((row["bytes"] + UNIT - 1) // UNIT * UNIT for row in inventory["files"])
        elif kind == "IMMUTABLE_GIT_REGULAR_PAYLOADS" and producer not in CANONICAL_PRODUCERS:
            refs = node.get("payloads")
            require(type(refs) is list and refs and len(refs) <= MAX_RECORDS,
                    "CAPACITY_IMMUTABLE_PAYLOAD_INVENTORY_REQUIRED")
            require(len({ref.get("uri") for ref in refs if type(ref) is dict}) == len(refs),
                    "CAPACITY_IMMUTABLE_PAYLOAD_DUPLICATED")
            amount = sum((len(_load_ref(reader, ref)) + UNIT - 1) // UNIT * UNIT for ref in refs)
        else:
            unsupported.append(name)
            continue
        costs[name] = {"quantity": quantity, "unit_bound_bytes": amount,
                       "allocated_bound_bytes": quantity * amount}
        require(all(type(node.get(key)) is int and node[key] == value for key, value in costs[name].items()),
                "CAPACITY_COMPONENT_BOUND_NOT_DERIVED_FROM_AUTHENTIC_BYTES")
    require(not unsupported, "CAPACITY_UNPROVED_DYNAMIC_GRAPH_COSTS:" + ",".join(sorted(unsupported)))
    return costs


def verify_comparison_proof(manifest, peaks, inventory, cheap, *, repo, get=None):
    """Standalone wrapper independently derives Source; no public bypass flag."""
    from scripts.rc6_material_pr_admission import frozen_source_inventory
    require(type(inventory) is dict and inventory.get("schema") == "porota.rc6.git-source-inventory.v1",
            "CAPACITY_FROZEN_GIT_SOURCE_INVENTORY_REQUIRED")
    sha, tree = inventory["source_sha"], inventory["source_tree"]
    exact = frozen_source_inventory(Path(repo), sha, tree)
    require(exact == inventory, "CAPACITY_EXACT_GIT_SOURCE_MANIFEST_DIGEST_MISMATCH")
    return _verify_with_fresh_inventory(manifest, peaks, exact, cheap, repo=repo, get=get)


def _verify_with_fresh_inventory(manifest, peaks, inventory, cheap, *, repo, get=None):
    """Private admission hook: consume its freshly derived authenticated Source.

    Only call immediately after admission.frozen_source_inventory, never with a
    receipt/index/workspace manifest. This avoids a second complete Source hash.
    There is no public boolean that skips Source authentication. Ownership,
    authorization and per-producer live filesystem checks stay with the caller.
    """
    from scripts.rc6_material_pr_admission import CAPACITY_COMPARISON_MANIFEST_SCHEMA, frozen_source_inventory
    from scripts.rc6_heavy_test_preflight import ENVELOPE_SCHEMA, validate_comparable_envelope
    require(type(manifest) is dict and manifest.get("schema") == CAPACITY_COMPARISON_MANIFEST_SCHEMA
            and manifest.get("status") == "VERIFIED"
            and type(manifest.get("unknown_components")) is list and not manifest["unknown_components"],
            "CAPACITY_COMPARISON_UNKNOWN_COMPONENTS_BEFORE_BOOTSTRAP")
    sha, tree = inventory["source_sha"], inventory["source_tree"]
    exact = inventory
    require(manifest.get("source_sha") == sha and manifest.get("source_tree") == tree
            and manifest.get("source_manifest_sha256") == digest(canonical(exact)),
            "CAPACITY_EXACT_GIT_SOURCE_MANIFEST_DIGEST_MISMATCH")
    require(manifest.get("cheap_files_sha256") == digest(canonical(cheap)),
            "CAPACITY_REVIEWED_CHEAP_GRAPH_FINGERPRINT_MISMATCH")
    require(type(peaks) is dict and bool(peaks) and all(type(value) is dict and value.get("schema") == ENVELOPE_SCHEMA
            for value in peaks.values()), "CAPACITY_COMPLETE_V2_ENVELOPES_REQUIRED")
    if set(peaks) & CANONICAL_PRODUCERS:
        require("bootstrap" in peaks, "CAPACITY_COMPOUND_BOOTSTRAP_ENVELOPE_REQUIRED")
    reader = GitEvidenceReader(get)
    require(reader.commit_tree(sha) == tree, "CAPACITY_AUTHENTICATED_SOURCE_TREE_REBOUND")
    authenticated = authenticate_records(manifest, reader)
    receipts = {}
    inventories = {(sha, tree): exact}
    for producer, peak in peaks.items():
        target = peak.get("target")
        require(type(target) is dict and target.get("producer") == producer, "CAPACITY_ENVELOPE_PRODUCER_REBOUND")
        bound = peak.get("admission_envelope_bytes")
        binding = {"candidate_sha": sha, "candidate_tree": tree, **target}
        validate_comparable_envelope(peak, binding, bound)
        comparison, reference = peak["comparison"], peak["reference"]
        require(comparison["source_manifest_sha256"] == digest(canonical(exact))
                and comparison["cheap_files_sha256"] == digest(canonical(cheap)),
                "CAPACITY_ENVELOPE_EXACT_SOURCE_OR_CHEAP_GRAPH_REBOUND")
        original_measurement(reference, reader)
        old_key = (reference["source_sha"], reference["source_tree"])
        if old_key not in inventories:
            inventories[old_key] = frozen_source_inventory(Path(repo), *old_key)
        old = inventories[old_key]
        require(reader.commit_tree(reference["source_sha"]) == reference["source_tree"],
                "CAPACITY_AUTHENTICATED_REFERENCE_SOURCE_TREE_REBOUND")
        delta = source_delta(old, exact)
        require(comparison.get("source_delta") == delta and comparison.get("source_delta_sha256") == digest(canonical(delta)),
                "CAPACITY_EXACT_COMPLETE_SOURCE_DELTA_REQUIRED")
        costs = []
        for key, source in (("reference_graph_evidence", old), ("target_graph_evidence", exact)):
            proof = comparison[key]
            require(proof["uri"] in authenticated, "CAPACITY_GRAPH_NOT_IN_AUTHENTICATED_RECORDS")
            raw = authenticated[proof["uri"]]
            require(digest(raw) == proof["sha256"], "CAPACITY_ORIGINAL_GRAPH_BYTES_REBOUND")
            graph = document(raw)
            require(graph.get("producer") == producer, "CAPACITY_ORIGINAL_GRAPH_PRODUCER_REBOUND")
            costs.append(recompute_costs(graph, source, reader=reader))
        before, after = costs
        require(sum(row["allocated_bound_bytes"] for row in before.values()) <= reference["allocated_bytes"],
                "CAPACITY_OBSERVED_SAMPLE_DOES_NOT_DOMINATE_REFERENCE_GRAPH")
        expected = {name: value for name, value in after.items()
                    if value["allocated_bound_bytes"] > before.get(name, {}).get("allocated_bound_bytes", 0)}
        declared = comparison["additional_bound_components"]
        require({row["name"] for row in declared} == set(expected), "CAPACITY_ADDITIONAL_COST_GRAPH_NOT_EXHAUSTIVE")
        extra = 0
        for row in declared:
            require(row["evidence"] == comparison["target_graph_evidence"],
                    "CAPACITY_ADDITION_EVIDENCE_NOT_AUTHENTIC_TARGET_GRAPH")
            _load_ref(reader, row["evidence"])
            name = row["name"]
            actual = expected[name]["allocated_bound_bytes"] - before.get(name, {}).get("allocated_bound_bytes", 0)
            require(row["allocated_bound_bytes"] == actual, "CAPACITY_DECLARED_ADDITION_NOT_AUTHENTIC_GRAPH_DELTA")
            extra += actual
        require(bound == reference["allocated_bytes"] + extra
                and sum(row["allocated_bound_bytes"] for row in after.values()) <= bound,
                "CAPACITY_COMPLETE_GRAPH_ENVELOPE_NOT_DOMINATING")
        receipts[producer] = {"admission_envelope_bytes": bound, "source_delta_sha256": digest(canonical(delta)),
            "producer_graph_sha256": comparison["producer_graph_sha256"],
            "reference_metric": reference["measurement_kind"], "reference_runner_class": reference["runner_class"],
            "reference_retained_entries": reference["retained_entries"], "recomputed_additional_bytes": extra}
    return {"schema": "porota.rc6.capacity-source-admission.v2", "status": "AUTHENTICATED_PROOF_BYTES_VERIFIED",
        "scope": "DIAGNOSTIC_PAYLOAD_BOUND_NO_RC6_ELIGIBILITY" if not (set(peaks) & CANONICAL_PRODUCERS) else "ORIGINAL_RC6_STAGE_COMPLETE",
        "source_sha": sha, "source_tree": tree, "source_manifest_sha256": digest(canonical(exact)),
        "producers": receipts, "authenticated_git_reads": reader.requests, "residual_reserve_bytes": RESERVE,
        "minimum_free_inode_ratio": 0.1, "live_filesystem_recheck_required": True,
        "native_quota_or_privileged_custody_proved": False, "RC6_eligibility_claimed": False,
        "launch_authorized": False, "G0_GREEN_claimed": False, "runtime_validated": False}
