#!/usr/bin/env python3
"""Fail-closed capacity receipt for RC6 heavy local/CI producers.

This command never deletes data. It only measures the selected filesystem and
emits a machine-readable admission receipt.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
from pathlib import Path
from typing import Any


DEFAULT_POLICY = Path("ops/policy/rc6-heavy-test-governance-v1.json")
BLOCKED_EXIT = 2


def load_policy(path: Path) -> dict[str, Any]:
    policy = json.loads(path.read_text(encoding="utf-8"))
    if policy.get("schema") != "porota.rc6.heavy-test-governance.v1":
        raise ValueError("unsupported or missing policy schema")
    return policy


def evaluate_capacity(
    *,
    free_bytes: int,
    total_inodes: int,
    free_inodes: int,
    expected_peak_bytes: int,
    residual_reserve_bytes: int,
    minimum_free_inode_ratio: float,
) -> dict[str, Any]:
    if expected_peak_bytes <= 0:
        raise ValueError("expected_peak_bytes must be a positive measured value")
    if residual_reserve_bytes < 0:
        raise ValueError("residual_reserve_bytes must be non-negative")
    if total_inodes <= 0:
        raise ValueError("filesystem inode total is unavailable")

    required_free_bytes = expected_peak_bytes + residual_reserve_bytes
    free_inode_ratio = free_inodes / total_inodes
    blockers: list[str] = []
    if free_bytes < required_free_bytes:
        blockers.append("CAPACITY_BYTES_INSUFFICIENT")
    if free_inode_ratio < minimum_free_inode_ratio:
        blockers.append("CAPACITY_INODES_INSUFFICIENT")

    return {
        "status": "GREEN" if not blockers else "BLOCKED",
        "blockers": blockers,
        "free_bytes": free_bytes,
        "expected_peak_bytes": expected_peak_bytes,
        "residual_reserve_bytes": residual_reserve_bytes,
        "required_free_bytes": required_free_bytes,
        "free_inode_ratio": free_inode_ratio,
        "minimum_free_inode_ratio": minimum_free_inode_ratio,
    }


def measure_filesystem(path: Path) -> dict[str, int]:
    stats = os.statvfs(path)
    return {
        "free_bytes": stats.f_bavail * stats.f_frsize,
        "total_inodes": stats.f_files,
        "free_inodes": stats.f_favail,
    }


def machine_snapshot() -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
    }
    try:
        snapshot["load_average_1m_5m_15m"] = list(os.getloadavg())
    except (AttributeError, OSError):
        snapshot["load_average_1m_5m_15m"] = None

    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        wanted = {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}
        values: dict[str, int] = {}
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            key, separator, remainder = line.partition(":")
            if separator and key in wanted:
                values[f"{key}_kib"] = int(remainder.strip().split()[0])
        snapshot.update(values)
    return snapshot


def build_receipt(
    *, policy: dict[str, Any], path: Path, expected_peak_bytes: int
) -> dict[str, Any]:
    measured = measure_filesystem(path)
    capacity_policy = policy["local_capacity"]
    result = evaluate_capacity(
        **measured,
        expected_peak_bytes=expected_peak_bytes,
        residual_reserve_bytes=int(capacity_policy["residual_reserve_bytes"]),
        minimum_free_inode_ratio=float(
            capacity_policy["minimum_free_inode_ratio"]
        ),
    )
    return {
        "schema": "porota.rc6.heavy-test-preflight-receipt.v1",
        "policy_version": policy["version"],
        "measured_path": str(path.resolve()),
        "capacity": result,
        "machine": machine_snapshot(),
        "destructive_action_performed": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=Path.cwd())
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument(
        "--expected-peak-bytes",
        type=int,
        required=True,
        help="Measured peak bytes from a comparable prior run; guesses are forbidden.",
    )
    parser.add_argument("--json-out", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        policy = load_policy(args.policy)
        receipt = build_receipt(
            policy=policy,
            path=args.path,
            expected_peak_bytes=args.expected_peak_bytes,
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        receipt = {
            "schema": "porota.rc6.heavy-test-preflight-receipt.v1",
            "capacity": {
                "status": "BLOCKED",
                "blockers": ["PREFLIGHT_EVIDENCE_INVALID"],
            },
            "error": f"{type(error).__name__}: {error}",
            "destructive_action_performed": False,
        }

    rendered = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.json_out:
        args.json_out.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if receipt["capacity"]["status"] == "GREEN" else BLOCKED_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
