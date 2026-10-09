#!/usr/bin/env python3
"""Read-only, fail-closed capacity admission; recheck live at each producer start.

Capacity evidence is neither a test PASS nor authorization to deploy. Historical
measurements must be cited and hashed; guesses and self-selected lower peaks are
not substitutions for a comparable producer's actual measurement.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).absolute().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import porota_predeploy_cleanup as custody

DEFAULT_POLICY = ROOT / "ops/policy/rc6-heavy-test-governance-v1.json"
SCHEMA = "porota.rc6.heavy-test-preflight-receipt.v2"
BLOCKED_EXIT = 2
BINDING_KEYS = {"candidate_sha", "candidate_tree", "producer", "attempt_id", "owner_id",
                "workload_fingerprint", "runner_class"}
ENVELOPE_SCHEMA = "porota.rc6.comparable-capacity-envelope.v2"
COMPARISON_CHECKS = {"all_scopes_accounted", "bootstrap_pip_fetch_full_source_accounted_if_in_scope",
    "control_and_log_allocations_accounted", "filesystem_allocation_model_validated", "fixture_clone_count_bounded",
    "new_fixture_payloads_bounded", "origin_runner_class_preserved", "same_metric_units", "source_delta_exact",
    "target_graph_enumerated"}


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def require(condition: bool, signature: str) -> None:
    if not condition:
        raise ValueError(signature)


def positive_int(value: Any, name: str, *, zero: bool = False) -> None:
    require(type(value) is int and value >= (0 if zero else 1), "PREFLIGHT_INVALID_" + name.upper())


def validate_policy(policy: dict[str, Any]) -> None:
    require(type(policy) is dict and policy.get("schema") == "porota.rc6.heavy-test-governance.v1",
            "PREFLIGHT_POLICY_SCHEMA_INVALID")
    positive_int(policy.get("version"), "policy_version")
    capacity = policy["local_capacity"]
    require(type(capacity.get("residual_reserve_bytes")) is int
            and capacity["residual_reserve_bytes"] >= 4 * 1024**3,
            "PREFLIGHT_RESERVE_CONTRACT_WEAKENED")
    ratio = capacity["minimum_free_inode_ratio"]
    require(type(ratio) in (int, float) and math.isfinite(ratio) and 0.1 <= ratio <= 1,
            "PREFLIGHT_INODE_CONTRACT_WEAKENED")
    age = capacity.get("maximum_receipt_age_seconds", 60)
    require(type(age) is int and 0 < age <= 60, "PREFLIGHT_FRESHNESS_CONTRACT_WEAKENED")
    expected = {"big_workload_catalog_instruments": 12000, "big_workload_observations": 60000,
                "hard_ceiling_seconds": 90, "qualification_target_seconds": 75,
                "maximum_rss_bytes": 2147483648, "maximum_evidence_bytes": 134217728,
                "maximum_retained_entries": 100000}
    require(all(type(policy["performance"].get(k)) is int and policy["performance"][k] == v
                for k, v in expected.items()), "PREFLIGHT_MATERIAL_CONTRACT_CHANGED")
    safety = policy["safety"]
    require(safety.get("mode") == "PRODUCTION_PAPER / SIMULATION"
            and type(safety.get("real_orders_sent")) is int and safety["real_orders_sent"] == 0
            and safety.get("real_order_routes") == "BLOCKED" and safety.get("ppi_watch") == "DO_NOT_TOUCH",
            "PREFLIGHT_PAPER_INVARIANT_CHANGED")


def load_policy(path: Path = DEFAULT_POLICY) -> dict[str, Any]:
    policy = custody.decode(path.read_bytes())
    validate_policy(policy)
    return policy


def validate_binding(binding: dict[str, Any]) -> None:
    require(type(binding) is dict and set(binding) == BINDING_KEYS, "PREFLIGHT_BINDING_REQUIRED")
    for name in ("candidate_sha", "candidate_tree"):
        require(type(binding[name]) is str and re.fullmatch(r"[0-9a-f]{40}", binding[name]) is not None,
                "PREFLIGHT_CANDIDATE_INVALID")
    require(type(binding["workload_fingerprint"]) is str
            and re.fullmatch(r"[0-9a-f]{64}", binding["workload_fingerprint"]) is not None,
            "PREFLIGHT_WORKLOAD_FINGERPRINT_REQUIRED")
    for name in ("producer", "attempt_id", "owner_id"):
        require(type(binding[name]) is str
                and re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", binding[name]) is not None,
                "PREFLIGHT_PRODUCER_ATTEMPT_OWNER_RUNNER_REQUIRED")
    require(type(binding["runner_class"]) is str
            and binding["runner_class"] in ("github-hosted/ubuntu-24.04", "DIAGNOSTIC"),
            "PREFLIGHT_CANONICAL_OR_DIAGNOSTIC_RUNNER_REQUIRED")


def _hash64(value, signature):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None, signature)


def _github_evidence_uri(uri):
    require(type(uri) is str and (uri.startswith("https://github.com/mbalbo2023/Porota-trading/")
            or uri.startswith("https://api.github.com/repos/mbalbo2023/Porota-trading/"))
            and len(uri) <= 2048, "PREFLIGHT_PEAK_EVIDENCE_URI_INVALID")


def validate_comparable_envelope(peak: dict[str, Any], binding: dict[str, Any], expected_peak_bytes: int) -> None:
    """Keep observed historical allocation distinct from a derived admission bound.

    This does not turn an observed closed sample into a temporal maximum or
    relabel its LOCAL origin as the target Actions runner. Unknown costs block.
    The caller's graph/source admission must also verify the cited manifest
    bytes; a capacity comparison object never carries dispatch privilege.
    """
    require(type(peak) is dict and peak.get("schema") == ENVELOPE_SCHEMA,
            "PREFLIGHT_COMPARABLE_ENVELOPE_REQUIRED")
    target = peak.get("target")
    require(type(target) is dict and set(target) == {"producer", "runner_class", "workload_fingerprint"}
            and all(target[name] == binding[name] for name in target), "PREFLIGHT_ENVELOPE_TARGET_REBOUND")
    require(peak.get("four_GiB_residual_reserve_excluded_from_envelope") is True
            and peak.get("local_measurement_relabelled_as_target_runner") is False
            and peak.get("reference_retention_GREEN_claimed") is False
            and peak.get("temporal_peak_guarantee_claimed") is False,
            "PREFLIGHT_ENVELOPE_HISTORICAL_OR_RESERVE_CLAIM_CHANGED")
    reference = peak.get("reference")
    require(type(reference) is dict and reference.get("measurement_kind") == "OBSERVED_ALLOCATED_HIGH_WATER"
            and reference.get("measurement_scope") == "OWN_CLOSED_RETAINED_NAMESPACE_OR_OBSERVED_COMPONENTS"
            and reference.get("measurement_complete") is True, "PREFLIGHT_REFERENCE_PARTIAL_OR_METRIC_INVALID")
    require(reference.get("runner_class") in ("LOCAL_MANAGED_WORKSPACE", "github-hosted/ubuntu-24.04", "DIAGNOSTIC")
            and type(reference.get("producer")) is str
            and re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", reference["producer"]) is not None,
            "PREFLIGHT_REFERENCE_ACTUAL_ORIGIN_REQUIRED")
    require(not (binding["runner_class"] != "DIAGNOSTIC" and reference["runner_class"] == "DIAGNOSTIC"),
            "PREFLIGHT_DIAGNOSTIC_REFERENCE_CANNOT_QUALIFY_CANONICAL_RUNNER")
    for key in ("source_sha", "source_tree"):
        require(type(reference.get(key)) is str and re.fullmatch(r"[0-9a-f]{40}", reference[key]) is not None,
                "PREFLIGHT_REFERENCE_SOURCE_INVALID")
    positive_int(reference.get("allocated_bytes"), "reference_allocated_bytes")
    positive_int(reference.get("retained_entries"), "reference_retained_entries", zero=True)
    evidence = reference.get("evidence")
    require(type(evidence) is dict, "PREFLIGHT_REFERENCE_RAW_EVIDENCE_REQUIRED")
    _github_evidence_uri(evidence.get("uri"))
    member = evidence.get("raw_member")
    require(type(member) is str and 0 < len(member) <= 4096 and not member.startswith("/")
            and all(part not in ("", ".", "..") for part in member.split("/")),
            "PREFLIGHT_REFERENCE_RAW_MEMBER_INVALID")
    for key in ("raw_member_sha256", "container_sha256", "measurement_sha256"):
        _hash64(evidence.get(key), "PREFLIGHT_REFERENCE_RAW_HASH_REQUIRED")
    measurement = evidence.get("measurement")
    require(type(measurement) is dict and len(canonical(measurement)) <= 65536
            and evidence["measurement_sha256"] == digest(measurement),
            "PREFLIGHT_REFERENCE_MEASUREMENT_DIGEST_MISMATCH")
    require(measurement.get("allocated_bytes") == reference["allocated_bytes"]
            and type(measurement.get("allocated_bytes")) is int
            and measurement.get("retained_entries") == reference["retained_entries"]
            and type(measurement.get("retained_entries")) is int
            and measurement.get("origin_runner_class") == reference["runner_class"]
            and measurement.get("scope") == reference["measurement_scope"],
            "PREFLIGHT_ORIGINAL_MEASUREMENT_OR_ORIGIN_CHANGED")
    comparison = peak.get("comparison")
    require(type(comparison) is dict and comparison.get("schema") == "porota.rc6.capacity-comparison-proof.v1"
            and comparison.get("candidate_sha") == binding["candidate_sha"]
            and comparison.get("candidate_tree") == binding["candidate_tree"]
            and comparison.get("dominance") == "REFERENCE_GRAPH_PLUS_ENUMERATED_BOUNDED_ADDITIONS",
            "PREFLIGHT_COMPARISON_CANDIDATE_OR_GRAPH_INVALID")
    for key in ("source_manifest_sha256", "cheap_files_sha256", "producer_graph_sha256"):
        _hash64(comparison.get(key), "PREFLIGHT_EXACT_SOURCE_OR_GRAPH_HASH_REQUIRED")
    checks = comparison.get("checks")
    require(type(checks) is dict and set(checks) == COMPARISON_CHECKS
            and all(value is True for value in checks.values()), "PREFLIGHT_COMPARISON_UNVERIFIED_CHECK")
    require(type(comparison.get("unknown_components")) is list and not comparison["unknown_components"],
            "PREFLIGHT_COMPARISON_UNKNOWN_COMPONENTS")
    for key in ("reference_graph_evidence", "target_graph_evidence"):
        graph = comparison.get(key)
        require(type(graph) is dict, "PREFLIGHT_GRAPH_EVIDENCE_REQUIRED")
        _github_evidence_uri(graph.get("uri"))
        _hash64(graph.get("sha256"), "PREFLIGHT_GRAPH_EVIDENCE_HASH_REQUIRED")
    require(comparison["target_graph_evidence"]["sha256"] == comparison["producer_graph_sha256"],
            "PREFLIGHT_TARGET_GRAPH_DIGEST_REBOUND")
    components = comparison.get("additional_bound_components")
    require(type(components) is list and len(components) <= 256, "PREFLIGHT_ADDITIONAL_COMPONENTS_REQUIRED")
    names, extra = set(), 0
    for component in components:
        require(type(component) is dict and type(component.get("name")) is str
                and re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", component["name"]) is not None
                and component["name"] not in names, "PREFLIGHT_COMPONENT_NAME_INVALID_OR_DUPLICATE")
        names.add(component["name"])
        require(component.get("model") == "CEIL4096_REGULAR_PAYLOAD_OR_EXACT_CODE_WRITE_BOUND"
                and type(component.get("assumptions")) is list and component["assumptions"]
                and all(type(value) is str and 0 < len(value) <= 2048 for value in component["assumptions"]),
                "PREFLIGHT_COMPONENT_MODEL_OR_ASSUMPTIONS_UNVERIFIED")
        for key in ("quantity", "unit_bound_bytes", "allocated_bound_bytes"):
            positive_int(component.get(key), "component_" + key, zero=True)
        require(component["allocated_bound_bytes"] == component["quantity"] * component["unit_bound_bytes"],
                "PREFLIGHT_COMPONENT_BOUND_ARITHMETIC_MISMATCH")
        proof = component.get("evidence")
        require(type(proof) is dict, "PREFLIGHT_COMPONENT_EVIDENCE_REQUIRED")
        _github_evidence_uri(proof.get("uri"))
        _hash64(proof.get("sha256"), "PREFLIGHT_COMPONENT_EVIDENCE_HASH_REQUIRED")
        extra += component["allocated_bound_bytes"]
    positive_int(peak.get("admission_envelope_bytes"), "admission_envelope_bytes")
    require(peak["admission_envelope_bytes"] == reference["allocated_bytes"] + extra == expected_peak_bytes,
            "PREFLIGHT_ADMISSION_ENVELOPE_BOUND_CHANGED")


def validate_comparable_peak(peak: dict[str, Any], binding: dict[str, Any], expected_peak_bytes: int) -> None:
    if type(peak) is dict and peak.get("schema") == ENVELOPE_SCHEMA:
        validate_comparable_envelope(peak, binding, expected_peak_bytes)
        return
    require(type(peak) is dict and peak.get("schema") == "porota.rc6.comparable-capacity-peak.v1",
            "PREFLIGHT_COMPARABLE_PEAK_REQUIRED")
    require(all(peak.get(name) == binding[name] for name in ("producer", "runner_class", "workload_fingerprint")),
            "PREFLIGHT_PEAK_NOT_COMPARABLE")
    positive_int(peak.get("peak_allocated_bytes"), "measured_peak")
    evidence = peak.get("evidence")
    require(type(evidence) is dict, "PREFLIGHT_PEAK_EVIDENCE_REQUIRED")
    uri = evidence.get("uri")
    require(type(uri) is str and (uri.startswith("https://github.com/mbalbo2023/Porota-trading/")
            or uri.startswith("https://api.github.com/repos/mbalbo2023/Porota-trading/"))
            and len(uri) <= 2048, "PREFLIGHT_PEAK_EVIDENCE_URI_INVALID")
    measurement = evidence.get("measurement")
    require(type(measurement) is dict and len(canonical(measurement)) <= 65536,
            "PREFLIGHT_PEAK_MEASUREMENT_REQUIRED")
    positive_int(measurement.get("allocated_bytes"), "allocated_bytes")
    positive_int(measurement.get("retained_entries"), "retained_entries", zero=True)
    require(evidence.get("sha256") == digest(measurement), "PREFLIGHT_PEAK_EVIDENCE_DIGEST_MISMATCH")
    require(expected_peak_bytes == peak["peak_allocated_bytes"] == measurement["allocated_bytes"],
            "PREFLIGHT_MEASURED_PEAK_CHANGED")


def envelope_allocated_bytes(peak: dict[str, Any]) -> int:
    """Select admission bytes without renaming an original observed measurement.

    This field adapter alone is not admission. build_receipt still validates
    the complete comparison, exact target binding and actual live filesystem.
    """
    require(type(peak) is dict and peak.get("schema") in
            (ENVELOPE_SCHEMA, "porota.rc6.comparable-capacity-peak.v1"), "PREFLIGHT_COMPARABLE_PEAK_REQUIRED")
    value = peak.get("admission_envelope_bytes" if peak["schema"] == ENVELOPE_SCHEMA else "peak_allocated_bytes")
    positive_int(value, "admission_envelope_bytes")
    return value


def evaluate_capacity(*, free_bytes: int, total_inodes: int, free_inodes: int,
                      expected_peak_bytes: int, residual_reserve_bytes: int,
                      minimum_free_inode_ratio: float) -> dict[str, Any]:
    for name, value in (("free_bytes", free_bytes), ("total_inodes", total_inodes),
                        ("free_inodes", free_inodes), ("expected_peak_bytes", expected_peak_bytes),
                        ("residual_reserve_bytes", residual_reserve_bytes)):
        positive_int(value, name, zero=name in ("free_bytes", "free_inodes", "residual_reserve_bytes"))
    require(free_inodes <= total_inodes, "PREFLIGHT_INODE_MEASUREMENT_INVALID")
    require(type(minimum_free_inode_ratio) in (int, float) and math.isfinite(minimum_free_inode_ratio)
            and 0 < minimum_free_inode_ratio <= 1, "PREFLIGHT_INODE_RATIO_INVALID")
    required = expected_peak_bytes + residual_reserve_bytes
    ratio = free_inodes / total_inodes
    blockers = (["CAPACITY_BYTES_INSUFFICIENT"] if free_bytes < required else [])
    if free_inodes < math.ceil(total_inodes * minimum_free_inode_ratio):
        blockers.append("CAPACITY_INODES_INSUFFICIENT")
    return {"status": "GREEN" if not blockers else "BLOCKED", "blockers": blockers,
            "free_bytes": free_bytes, "total_inodes": total_inodes, "free_inodes": free_inodes,
            "expected_peak_bytes": expected_peak_bytes, "residual_reserve_bytes": residual_reserve_bytes,
            "required_free_bytes": required, "free_inode_ratio": ratio,
            "minimum_free_inode_ratio": minimum_free_inode_ratio}


def measure_filesystem(path: Path) -> dict[str, Any]:
    path = Path(path).absolute()
    with custody.directory(path) as fd:
        details = os.fstat(fd)
        stats = os.fstatvfs(fd)
        mount = custody.mount_id(fd)
        # A bind mount can share st_dev. Match the opened inode's actual Linux
        # mount, then preserve its allocation type and unit in the receipt.
        raw = Path("/proc/self/mountinfo").read_bytes()
        require(len(raw) <= 1024**2, "PREFLIGHT_MOUNT_INFORMATION_LIMIT")
        rows = []
        for line in raw.decode("utf-8", errors="strict").splitlines():
            before, separator, after = line.partition(" - ")
            fields, filesystem_fields = before.split(), after.split()
            require(separator and len(fields) >= 6 and len(filesystem_fields) >= 3,
                    "PREFLIGHT_MOUNT_INFORMATION_INVALID")
            if fields[0] == str(mount):
                rows.append((fields, filesystem_fields))
        require(len(rows) == 1, "PREFLIGHT_MOUNT_INFORMATION_AMBIGUOUS")
        fields, filesystem_fields = rows[0]
        require(fields[2] == str(os.major(details.st_dev)) + ":" + str(os.minor(details.st_dev)),
                "PREFLIGHT_MOUNT_DEVICE_MISMATCH")
        return {"free_bytes": stats.f_bavail * stats.f_frsize, "total_inodes": stats.f_files,
                "free_inodes": stats.f_favail, "filesystem_device": details.st_dev,
                "mount_id": mount, "filesystem_type": filesystem_fields[0],
                "allocation_unit_bytes": stats.f_frsize, "preferred_io_block_bytes": stats.f_bsize,
                "path_identity": [details.st_dev, details.st_ino, details.st_uid, details.st_gid,
                                  details.st_mode]}


def machine_snapshot() -> dict[str, Any]:
    result = {"platform": platform.platform(), "cpu_count": os.cpu_count(),
              "python_version": platform.python_version(), "runner_arch": os.environ.get("RUNNER_ARCH"),
              "runner_os": os.environ.get("RUNNER_OS"), "github_actions": os.environ.get("GITHUB_ACTIONS") == "true"}
    try:
        result["load_average_1m_5m_15m"] = list(os.getloadavg())
    except (AttributeError, OSError):
        result["load_average_1m_5m_15m"] = None
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, _, value = line.partition(":")
        if key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
            result[key + "_kib"] = int(value.split()[0])
    return result


def build_receipt(*, policy: dict[str, Any], path: Path, expected_peak_bytes: int,
                  binding: dict[str, Any] | None = None,
                  comparable_peak: dict[str, Any] | None = None) -> dict[str, Any]:
    validate_policy(policy)
    validate_binding(binding)
    positive_int(expected_peak_bytes, "expected_peak_bytes")
    validate_comparable_peak(comparable_peak, binding, expected_peak_bytes)
    measured = measure_filesystem(path)
    if comparable_peak["schema"] == ENVELOPE_SCHEMA:
        require(type(measured.get("allocation_unit_bytes")) is int and measured["allocation_unit_bytes"] == 4096,
                "PREFLIGHT_COMPARISON_ALLOCATION_MODEL_NOT_LIVE_VALIDATED")
        if binding["runner_class"] == "github-hosted/ubuntu-24.04":
            require(measured.get("filesystem_type") == "ext4",
                    "PREFLIGHT_COMPARISON_FILESYSTEM_TYPE_NOT_CANONICAL")
        else:
            require(type(measured.get("filesystem_type")) is str and measured["filesystem_type"],
                    "PREFLIGHT_COMPARISON_FILESYSTEM_TYPE_UNKNOWN")
    settings = policy["local_capacity"]
    capacity = evaluate_capacity(**{k: measured[k] for k in ("free_bytes", "total_inodes", "free_inodes")},
        expected_peak_bytes=expected_peak_bytes, residual_reserve_bytes=settings["residual_reserve_bytes"],
        minimum_free_inode_ratio=settings["minimum_free_inode_ratio"])
    receipt = {"schema": SCHEMA, "policy_version": policy["version"], "policy_sha256": digest(policy),
        "measured_path": str(Path(path).absolute()), "measured_at_unix_ns": time.time_ns(),
        "measurement_pid": os.getpid(), "measurement_boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "binding": dict(binding), "comparable_peak": comparable_peak, "capacity": capacity,
        "filesystem": measured, "machine": machine_snapshot(), "destructive_action_performed": False,
        "admission_basis": ("OBSERVED_ALLOCATION_PLUS_VERIFIED_BOUNDED_ADDITIONS"
                            if comparable_peak["schema"] == ENVELOPE_SCHEMA else "COMPARABLE_MEASURED_PEAK"),
        "real_orders_sent": 0, "promotion_or_runtime_validation_claimed": False}
    receipt["receipt_sha256"] = digest(receipt)
    return receipt


def validate_live_receipt(*, path: Path, receipt: dict[str, Any], binding: dict[str, Any],
                          policy: dict[str, Any]) -> dict[str, Any]:
    """Do not use a prior G0 check as the check immediately before subprocess."""
    validate_policy(policy)
    validate_binding(binding)
    require(type(receipt) is dict and receipt.get("schema") == SCHEMA
            and receipt.get("receipt_sha256") == digest({k: v for k, v in receipt.items() if k != "receipt_sha256"}),
            "PREFLIGHT_RECEIPT_DIGEST_INVALID")
    require(receipt.get("binding") == binding and receipt.get("policy_sha256") == digest(policy)
            and receipt.get("measured_path") == str(Path(path).absolute()), "PREFLIGHT_RECEIPT_BINDING_MISMATCH")
    measured_at = receipt.get("measured_at_unix_ns")
    positive_int(measured_at, "measurement_time")
    age = time.time_ns() - measured_at
    require(0 <= age <= policy["local_capacity"].get("maximum_receipt_age_seconds", 60) * 10**9,
            "PREFLIGHT_RECEIPT_STALE")
    require(receipt.get("measurement_boot_id") == Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "PREFLIGHT_RECEIPT_DIFFERENT_BOOT")
    require(receipt["capacity"].get("status") == "GREEN", "PREFLIGHT_PRIOR_CAPACITY_RED")
    peak = receipt["capacity"]["expected_peak_bytes"]
    validate_comparable_peak(receipt["comparable_peak"], binding, peak)
    measured = measure_filesystem(path)
    require(all(measured[k] == receipt["filesystem"].get(k)
                for k in ("filesystem_device", "mount_id", "path_identity", "filesystem_type",
                          "allocation_unit_bytes")), "PREFLIGHT_FILESYSTEM_REBOUND")
    live = build_receipt(policy=policy, path=path, expected_peak_bytes=peak,
                         binding=binding, comparable_peak=receipt["comparable_peak"])
    require(live["capacity"]["status"] == "GREEN", "PREFLIGHT_LIVE_CAPACITY_INSUFFICIENT")
    live["prior_receipt_sha256"] = receipt["receipt_sha256"]
    live["receipt_sha256"] = digest({k: v for k, v in live.items() if k != "receipt_sha256"})
    return live


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=Path.cwd())
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--expected-peak-bytes", type=int, required=True)
    parser.add_argument("--binding-json", type=Path, required=True)
    parser.add_argument("--comparable-peak-json", type=Path, required=True)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args(argv)
    try:
        receipt = build_receipt(policy=load_policy(args.policy), path=args.path,
            expected_peak_bytes=args.expected_peak_bytes, binding=custody.decode(args.binding_json.read_bytes()),
            comparable_peak=custody.decode(args.comparable_peak_json.read_bytes()))
    except (OSError, KeyError, TypeError, ValueError) as error:
        receipt = {"schema": SCHEMA, "capacity": {"status": "BLOCKED", "blockers": ["PREFLIGHT_EVIDENCE_INVALID"]},
                   "error": str(error), "destructive_action_performed": False, "real_orders_sent": 0}
    if args.json_out:
        custody.publish(args.json_out.absolute(), receipt)
    print(canonical(receipt).decode(), end="")
    return 0 if receipt["capacity"]["status"] == "GREEN" else BLOCKED_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
