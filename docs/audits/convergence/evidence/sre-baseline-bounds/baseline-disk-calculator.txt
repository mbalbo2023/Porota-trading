#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_policy(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if int(data.get("schema_version", 0)) != 1:
        raise ValueError("unsupported disk policy schema")
    return data


def required_pretransfer_free(
    image_tar_bytes: int,
    image_unpacked_bytes: int,
    bundle_bytes: int,
    policy: dict,
) -> int:
    deploy = policy["deploy"]["pretransfer_formula"]
    disk = policy["disk"]
    calculated = (
        int(image_tar_bytes) * int(deploy["image_tar_multiplier"])
        + int(image_unpacked_bytes) * int(deploy["image_unpacked_multiplier"])
        + int(bundle_bytes) * int(deploy["bundle_multiplier"])
        + int(deploy["fixed_headroom_bytes"])
    )
    return max(int(disk["deploy_pretransfer_min_free_bytes"]), calculated)


def evaluate(
    available_bytes: int,
    inode_free_percent: float,
    required_bytes: int,
    policy: dict,
) -> tuple[bool, dict]:
    min_inode = float(policy["disk"]["min_inode_free_percent"])
    ok_space = int(available_bytes) >= int(required_bytes)
    ok_inode = float(inode_free_percent) >= min_inode
    payload = {
        "available_bytes": int(available_bytes),
        "required_bytes": int(required_bytes),
        "inode_free_percent": float(inode_free_percent),
        "min_inode_free_percent": min_inode,
        "space_ok": ok_space,
        "inode_ok": ok_inode,
        "status": "GREEN" if ok_space and ok_inode else "RED",
    }
    return ok_space and ok_inode, payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", required=True)
    ap.add_argument("--image-tar-bytes", type=int, required=True)
    ap.add_argument("--image-unpacked-bytes", type=int, required=True)
    ap.add_argument("--bundle-bytes", type=int, required=True)
    ap.add_argument("--available-bytes", type=int)
    ap.add_argument("--inode-free-percent", type=float)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    policy = load_policy(args.policy)
    required = required_pretransfer_free(
        args.image_tar_bytes, args.image_unpacked_bytes, args.bundle_bytes, policy
    )

    if args.available_bytes is None or args.inode_free_percent is None:
        print(f"RC6_DISK_REQUIRED_PRETRANSFER_FREE={required}")
        return 0

    ok, payload = evaluate(
        args.available_bytes,
        args.inode_free_percent,
        required,
        policy,
    )
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    print(
        "RC6_DISK_PRETRANSFER_PREFLIGHT="
        f"{payload['status']}|available={payload['available_bytes']}"
        f"|required={payload['required_bytes']}"
        f"|inode_free_percent={payload['inode_free_percent']:.2f}"
        f"|inode_required_percent={payload['min_inode_free_percent']:.2f}"
    )
    return 0 if ok else 42


if __name__ == "__main__":
    raise SystemExit(main())
