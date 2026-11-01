"""A222 offline CI phase timing from sanitized Actions step timestamps.

No raw logs, token, network, job mutation or timing-based GO is involved.
Only an explicit, allowlisted set of phases is reported.  This pilot is not
wired into Predeploy until the Actions API/job receipt contract is verified.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
from zoneinfo import ZoneInfo


PHASES = {
    "Fast host-transition incident replay": "EARLY_REPLAY",
    "Governed automatic test discovery and execution": "GOVERNED_TESTS",
    "Build candidate exactly once": "DOCKER_BUILD_ONCE",
    "Exact image native sizing and generated catalog proof": "NATIVE_PROOF",
    "Upload predeploy evidence": "ARTIFACT_UPLOAD",
}


class PhaseMetricsInvalid(ValueError):
    pass


def _invalid() -> None:
    raise PhaseMetricsInvalid("RC6_CI_PHASE_METRICS_INPUT_INVALID")


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not 15 <= len(value) <= 40:
        _invalid()
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _invalid()
    if stamp.tzinfo is None or stamp.utcoffset() != timezone.utc.utcoffset(stamp):
        _invalid()
    return stamp.astimezone(timezone.utc)


def analyze(payload: object) -> dict:
    """Produce conservative observed timings; missing phases remain unverified."""
    if not isinstance(payload, dict) or set(payload) != {
            "schema_version", "run_id", "source_sha", "steps"}:
        _invalid()
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        _invalid()
    run_id, sha = payload["run_id"], payload["source_sha"]
    if type(run_id) is not int or not 0 < run_id < 10**17:
        _invalid()
    if not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{40}", sha) is None:
        _invalid()
    steps = payload["steps"]
    if not isinstance(steps, list) or len(steps) > 256:
        _invalid()
    results = {}
    seen_names = set()
    unknown = 0
    for raw in steps:
        if not isinstance(raw, dict) or set(raw) != {
                "name", "status", "conclusion", "started_at", "completed_at"}:
            _invalid()
        name = raw["name"]
        if not isinstance(name, str) or not 1 <= len(name) <= 160 or name in seen_names:
            _invalid()
        seen_names.add(name)
        if (not isinstance(raw["status"], str) or
                raw["status"] not in {"queued", "in_progress", "completed"} or
                (raw["conclusion"] is not None and
                 not isinstance(raw["conclusion"], str)) or
                raw["conclusion"] not in {None, "success", "failure", "cancelled", "skipped"}):
            _invalid()
        if name not in PHASES:
            unknown += 1
            continue  # No dynamic step label is copied to public evidence.
        key = PHASES[name]
        if raw["status"] != "completed" or raw["conclusion"] is None:
            results[key] = {"state": "NO_VERIFICADO"}
            continue
        if raw["started_at"] is None or raw["completed_at"] is None:
            results[key] = {"state": "NO_VERIFICADO"}
            continue
        start, end = _timestamp(raw["started_at"]), _timestamp(raw["completed_at"])
        if end < start:
            _invalid()
        seconds = round((end - start).total_seconds(), 3)
        results[key] = {
            "state": "MEASURED", "conclusion": raw["conclusion"],
            "started_at_utc": start.isoformat().replace("+00:00", "Z"),
            "completed_at_utc": end.isoformat().replace("+00:00", "Z"),
            "started_at_art": start.astimezone(ZoneInfo("America/Argentina/Buenos_Aires")).isoformat(),
            "completed_at_art": end.astimezone(ZoneInfo("America/Argentina/Buenos_Aires")).isoformat(),
            "elapsed_seconds": seconds,
        }
    measured = [x for x in results.values() if x["state"] == "MEASURED"]
    return {"schema_version": 1, "run_id": run_id, "source_sha": sha,
            "analysis_mode": "READ_ONLY", "automatic_go": False,
            "phase_count_expected": len(PHASES),
            "phase_count_measured": len(measured),
            "unknown_step_count": unknown,
            "observed_phase_seconds_sum": round(sum(x["elapsed_seconds"] for x in measured), 3),
            "timing_status": "COMPLETE_PHASE_SAMPLE" if len(measured) == len(PHASES)
                             else "PARTIAL_PHASE_SAMPLE",
            "phases": {k: results.get(k, {"state": "NO_VERIFICADO"}) for k in PHASES.values()}}
