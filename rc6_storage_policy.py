"""POROTA RC6 dynamic storage headroom policy.

Pure calculation module. It never deletes data and never mutates the host.
The shell gate in scripts/rc6_storage_headroom_gate.sh supplies live metrics.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from math import ceil
from typing import Dict

GIB = 1024 ** 3
MIB = 1024 ** 2


@dataclass(frozen=True)
class StorageDecision:
    mode: str
    total_bytes: int
    free_bytes: int
    image_bytes: int
    projected_write_bytes: int
    operational_reserve_bytes: int
    build_reserve_bytes: int
    deploy_pre_required_bytes: int
    deploy_post_required_bytes: int
    ingest_required_bytes: int
    hard_stop_bytes: int
    decision: str
    reason: str

    def as_dict(self) -> Dict[str, object]:
        return asdict(self)


def calculate_storage_decision(
    *,
    total_bytes: int,
    free_bytes: int,
    image_bytes: int,
    mode: str,
    projected_write_bytes: int = 512 * MIB,
) -> StorageDecision:
    """Calculate deploy/ingest headroom without any host mutation.

    Policy:
    - Operational reserve: max(4 GiB, 20% of filesystem).
    - Build reserve: max(3 GiB, 2x current/reference image size).
    - Deploy pre-build: operational + build reserve.
    - Deploy post-build: operational reserve only.
    - Ingestion: preserve deploy-pre headroom plus projected next write batch.
    - Hard stop marker: max(2 GiB, 10% of filesystem).

    Ingestion returns PAUSE rather than BLOCK because it is resumable background
    work. Deploy modes return BLOCK when the relevant headroom is unavailable.
    """
    mode = str(mode).strip().lower()
    valid = {"deploy-pre", "deploy-post", "ingest-start", "ingest-continue", "report"}
    if mode not in valid:
        raise ValueError(f"unsupported mode: {mode}")
    for name, value in {
        "total_bytes": total_bytes,
        "free_bytes": free_bytes,
        "image_bytes": image_bytes,
        "projected_write_bytes": projected_write_bytes,
    }.items():
        if int(value) < 0:
            raise ValueError(f"{name} must be >= 0")
    if int(total_bytes) <= 0:
        raise ValueError("total_bytes must be > 0")
    if int(free_bytes) > int(total_bytes):
        raise ValueError("free_bytes cannot exceed total_bytes")

    total_bytes = int(total_bytes)
    free_bytes = int(free_bytes)
    image_bytes = int(image_bytes)
    projected_write_bytes = int(projected_write_bytes)

    operational = max(4 * GIB, ceil(total_bytes * 0.20))
    build = max(3 * GIB, 2 * image_bytes)
    deploy_pre = operational + build
    deploy_post = operational
    ingest_required = deploy_pre + projected_write_bytes
    hard_stop = max(2 * GIB, ceil(total_bytes * 0.10))

    if mode == "report":
        decision, reason = "REPORT", "observation_only"
    elif free_bytes < hard_stop:
        decision = "PAUSE" if mode.startswith("ingest-") else "BLOCK"
        reason = "hard_stop_headroom"
    elif mode == "deploy-pre":
        decision = "ALLOW" if free_bytes >= deploy_pre else "BLOCK"
        reason = "deploy_pre_headroom_ok" if decision == "ALLOW" else "deploy_pre_headroom_insufficient"
    elif mode == "deploy-post":
        decision = "ALLOW" if free_bytes >= deploy_post else "BLOCK"
        reason = "deploy_post_operational_reserve_ok" if decision == "ALLOW" else "deploy_post_operational_reserve_insufficient"
    else:
        decision = "ALLOW" if free_bytes >= ingest_required else "PAUSE"
        reason = "ingest_preserves_deploy_headroom" if decision == "ALLOW" else "ingest_backpressure_preserve_deploy_headroom"

    return StorageDecision(
        mode=mode,
        total_bytes=total_bytes,
        free_bytes=free_bytes,
        image_bytes=image_bytes,
        projected_write_bytes=projected_write_bytes,
        operational_reserve_bytes=operational,
        build_reserve_bytes=build,
        deploy_pre_required_bytes=deploy_pre,
        deploy_post_required_bytes=deploy_post,
        ingest_required_bytes=ingest_required,
        hard_stop_bytes=hard_stop,
        decision=decision,
        reason=reason,
    )
