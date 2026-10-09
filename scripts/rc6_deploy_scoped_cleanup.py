#!/usr/bin/env python3
"""Remove only unreferenced RC6 candidate tags; shared Docker caches stay intact."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess

IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
CANDIDATE_TAG = re.compile(r"porota-trading-bot:17\.0\.0-rc6-candidate-[0-9a-f]{40}")
STABLE = "porota-trading-bot:17.0.0-rc6"


def docker(*args):
    result = subprocess.run(["docker", *args], check=True, capture_output=True, text=True, timeout=20)
    return result.stdout.strip()


def cleanup(*, pinned_ids=(), run_docker=docker, disk_probe=lambda: shutil.disk_usage("/").free):
    pins = set(pinned_ids)
    if any(IMAGE_ID.fullmatch(value) is None for value in pins):
        raise ValueError("SCOPED_CLEANUP_PIN_INVALID")
    before = disk_probe()
    stable = run_docker("image", "inspect", "--format", "{{.Id}}", STABLE)
    if IMAGE_ID.fullmatch(stable) is None:
        raise ValueError("SCOPED_CLEANUP_STABLE_ID_INVALID")
    pins.add(stable)
    # Enumerate this namespace only. No systemd inspection, data/volume access,
    # global prune, or foreign container/image inspection occurs here.
    inventory = run_docker("image", "ls", "--no-trunc", "--filter",
        "reference=porota-trading-bot:17.0.0-rc6-candidate-*", "--format", "{{.Repository}}:{{.Tag}}|{{.ID}}")
    removed, retained = [], []
    for line in inventory.splitlines():
        tag, separator, identity = line.partition("|")
        if not separator or CANDIDATE_TAG.fullmatch(tag) is None or IMAGE_ID.fullmatch(identity) is None:
            raise ValueError("SCOPED_CLEANUP_IMAGE_INVENTORY_INVALID")
        # A stopped container also pins the image. The ancestor filter is
        # conservative and returns identifiers only, without foreign metadata.
        users = run_docker("ps", "-aq", "--filter", "ancestor=" + identity)
        if identity in pins or users:
            retained.append(tag)
            continue
        run_docker("image", "rm", tag)  # Never --force: a concurrent owner blocks removal.
        removed.append(tag)
    after = disk_probe()
    return {"schema": "rc6.scoped-image-cleanup.v1", "status": "GREEN",
            "removed_candidate_tags": removed, "retained_candidate_tags": retained,
            "disk_before": before, "disk_after": after, "space_recovered": after - before,
            "shared_cache_action": "UNTOUCHED_NO_HOST_BUILD", "global_prune": "NOT_CALLED",
            "ppi_watch": "UNTOUCHED_NOT_INSPECTED"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pin-image-id", action="append", default=[])
    args = parser.parse_args(argv)
    result = cleanup(pinned_ids=args.pin_image_id)
    print(json.dumps(result, sort_keys=True))
    print("RC6_SCOPED_CANDIDATE_CLEANUP=GREEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
