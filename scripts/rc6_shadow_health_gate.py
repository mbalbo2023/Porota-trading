#!/usr/bin/env python3
"""Read-only post-start gate for the required SHADOW child and fresh V2 cut."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat

HEALTH_SCHEMA = "rc6.runtime-child-health.v1"
GENERATION_SCHEMA = "rc6.shadow-evidence-generation.v2"


class ShadowHealthRejected(ValueError):
    pass


def timestamp(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError
        return result.astimezone(timezone.utc)
    except (TypeError, ValueError) as exc:
        raise ShadowHealthRejected("SHADOW_HEALTH_CLOCK_INVALID") from exc


def process_exists(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def validate_health_cut(health, generation, *, source_sha, tree_sha, now,
                        configuration_fingerprint, max_health_age=20, max_generation_age=90,
                        process_probe=process_exists):
    """Healthy scanner/parent cannot substitute for child or generation health."""
    if (health.get("schema") != HEALTH_SCHEMA or health.get("source_sha") != source_sha
            or health.get("candidate_tree_sha") != tree_sha
            or not 0 <= (now - timestamp(health.get("recorded_at"))).total_seconds() <= max_health_age):
        raise ShadowHealthRejected("SHADOW_CHILD_HEALTH_STALE_OR_WRONG_SOURCE")
    child = health.get("children", {}).get("dynamic_shadow", {})
    if (child.get("state") != "RUNNING" or type(child.get("pid")) is not int or child["pid"] <= 0
            or type(child.get("restarts")) is not int or child["restarts"] < 0
            or type(child.get("spawn_failures")) is not int or child["spawn_failures"] < 0
            or not process_probe(child["pid"])):
        raise ShadowHealthRejected("SHADOW_CHILD_NOT_HEALTHY")
    manifest = generation.get("manifest", {})
    report, status = generation.get("report", {}), generation.get("status", {})
    if (manifest.get("schema") != GENERATION_SCHEMA
            or type(manifest.get("sequence")) is not int or manifest["sequence"] <= 0
            or manifest.get("configuration_fingerprint") != configuration_fingerprint
            or any(value.get("configuration_fingerprint") != configuration_fingerprint for value in (report, status))):
        raise ShadowHealthRejected("SHADOW_GENERATION_CONFIG_OR_SCHEMA_MISMATCH")
    as_of = timestamp(report.get("as_of"))
    started_at = timestamp(child.get("started_at"))
    if (not 0 <= (now - as_of).total_seconds() <= max_generation_age
            or as_of < started_at or started_at > now
            or child["restarts"] > 0 and (now - started_at).total_seconds() < 60
            or timestamp(status.get("as_of")) != as_of
            or timestamp(manifest.get("source_watermark", {}).get("as_of")) != as_of):
        raise ShadowHealthRejected("SHADOW_GENERATION_MISSING_FRESH_PROGRESS")
    for value in (report, status):
        if (value.get("mode") != "SHADOW" or type(value.get("real_orders_sent")) is not int
                or value["real_orders_sent"] != 0 or value.get("real_routes") != "NOT_CALLED"):
            raise ShadowHealthRejected("SHADOW_GENERATION_SAFETY_MISMATCH")
    if report.get("phase") not in {"PREOPEN", "OPEN", "CLOSED"}:
        raise ShadowHealthRejected("SHADOW_GENERATION_PHASE_INVALID")
    return {"schema": "rc6.shadow-post-start-health.v1", "status": "GREEN",
            "source_sha": source_sha, "candidate_tree_sha": tree_sha,
            "child_pid": child["pid"], "child_restarts": child["restarts"],
            "child_spawn_failures": child["spawn_failures"],
            "generation_id": manifest.get("generation_id"), "sequence": manifest["sequence"],
            "configuration_fingerprint": configuration_fingerprint,
            "generation_as_of": as_of.isoformat(), "generation_phase": report["phase"],
            "readiness": {"OPEN": "SHADOW_OPEN_EVIDENCE", "PREOPEN": "PREOPEN_NON_OPERATIONAL",
                          "CLOSED": "CLOSED_NON_OPERATIONAL"}[report["phase"]],
            "provider_capacity_open": "NO_VERIFICADO", "real_orders_sent": 0, "real_routes": "NOT_CALLED"}


def read_health(database, *, source_sha, tree_sha, now=None, process_probe=process_exists):
    from cg_paper_workspace import artifact_root
    from rc6_shadow_runtime.persistence import read_committed_generation, shadow_evidence_root
    from rc6_shadow_runtime.worker import ShadowRuntime
    now = now or datetime.now(timezone.utc)
    path = artifact_root(database) / "runtime-health.json"
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 64 * 1024:
        raise ShadowHealthRejected("SHADOW_CHILD_HEALTH_FILE_INVALID")
    from scripts.porota_artifact_provenance import decode_json
    health = decode_json(path.read_bytes())
    root = shadow_evidence_root(database)
    generation = read_committed_generation(root)
    worker = ShadowRuntime.from_environment(database)
    fingerprint = worker.configuration_fingerprint(now.isoformat())
    return validate_health_cut(health, generation, source_sha=source_sha, tree_sha=tree_sha,
                               now=now, configuration_fingerprint=fingerprint, process_probe=process_probe)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--tree-sha", required=True)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args(argv)
    if any(re.fullmatch(r"[0-9a-f]{40}", value) is None for value in (args.source_sha, args.tree_sha)):
        parser.error("source/tree must be exact Git identities")
    try:
        result = read_health(args.database, source_sha=args.source_sha, tree_sha=args.tree_sha)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        # Exceptions may contain source paths or data. Only the bounded taxonomy
        # reaches logs; health failure never claims readiness or provider capacity.
        result = {"schema": "rc6.shadow-post-start-health.v1", "status": "RED",
                  "reason": "SHADOW_CHILD_OR_GENERATION_NOT_HEALTHY", "real_orders_sent": 0}
    if args.json_out:
        args.json_out.write_text(json.dumps(result, sort_keys=True) + "\n")
    print(json.dumps(result, sort_keys=True))
    print("RC6_SHADOW_RUNTIME_HEALTH=" + result["status"])
    return 0 if result["status"] == "GREEN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
