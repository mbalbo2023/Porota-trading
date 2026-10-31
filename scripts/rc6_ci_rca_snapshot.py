"""Sanitized automatic Actions step-outcome -> deterministic RCA fingerprint.

This is an offline, NON-AUTHORITATIVE diagnostic. It accepts only named step
outcomes and never reads CI logs, source credentials, orders, account IDs,
runtime host state or raw test data. GitHub's `failure` outcome establishes
only a failed *phase*, not its root cause: `error_code` stays UNKNOWN. Human
review must explicitly register RCA -> fix commit -> guard -> regression test.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile

from scripts import rc6_rca_learning as rca


PHASES = {
    "pr_provenance": ("ci_provenance", "CI_PR_PROVENANCE"),
    "early_replay": ("ci_replay", "CI_EARLY_REPLAY"),
    "governed_suite": ("ci_tests", "CI_GOVERNED_TESTS"),
    "parallel_pilot": ("ci_tests", "CI_PARALLEL_PILOT"),
    "build_once": ("ci_build", "CI_BUILD_ONCE"),
    "native_proof": ("ci_native", "CI_NATIVE_PROOF"),
    "artifact_gate": ("ci_artifact", "CI_ARTIFACT_GATE"),
}
OUTCOMES = frozenset({"success", "failure", "skipped", "cancelled", "UNOBSERVED"})


def snapshot(payload: object, registry: object | None = None) -> dict:
    if (not isinstance(payload, dict) or set(payload) != {
            "schema_version", "run_id", "source_sha", "observed_at_utc", "outcomes"}):
        rca._reject()
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        rca._reject()
    outcomes = payload["outcomes"]
    if not isinstance(outcomes, dict) or set(outcomes) != set(PHASES):
        rca._reject()
    for state in outcomes.values():
        if not isinstance(state, str) or state not in OUTCOMES:
            rca._reject()
    run_id = payload["run_id"]
    if type(run_id) is not int or not 0 < run_id < 10**17:
        rca._reject()
    source_sha = rca._hex(payload["source_sha"], 40)
    observed_utc, observed_art = rca._utc(payload["observed_at_utc"])
    events = []
    for phase in sorted(PHASES):
        if outcomes[phase] == "failure":
            component, label = PHASES[phase]
            events.append({"component": component, "failure_phase": label,
                           "error_code": "UNKNOWN", "run_id": run_id,
                           "source_sha": source_sha, "timestamp_utc": observed_utc})
    diagnostic = rca.analyze({"schema_version": 1, "events": events}, registry)
    if diagnostic["confirmed_rca_count"] != 0 or diagnostic["go_decision"] != "NO_AUTOMATIC_GO":
        rca._reject()
    return {"schema_version": 1, "mode": "STRUCTURED_CI_FAILURE_DIAGNOSTIC_ONLY",
            "run_id": run_id, "source_sha": source_sha,
            "observed_at_utc": observed_utc, "observed_at_art": observed_art,
            "observed_outcomes": {key: outcomes[key] for key in sorted(PHASES)},
            "phase_failures": len(events),
            "coverage": "FULL" if all(value != "UNOBSERVED" for value in outcomes.values())
                        else "PARTIAL_NO_VERIFICADO",
            "root_cause_verified": False, "automatic_go": False,
            "auto_merge": False, "auto_deploy": False, "auto_trade": False,
            "rca": diagnostic}


def _write(path: Path, receipt: dict) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        rca._reject()
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=path.parent, prefix=".rc6-ci-rca-",
                                         delete=False) as stream:
            temp_path = Path(stream.name)
            os.chmod(temp_path, 0o600)
            json.dump(receipt, stream, sort_keys=True, indent=2)
            stream.write("\n")
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--registry", type=Path,
                        default=Path(__file__).resolve().parents[1] /
                        "ops/policy/rc6-rca-signatures-v1.json")
    args = parser.parse_args(argv)
    try:
        outcome = {key: os.environ.get(f"RC6_CI_OUTCOME_{key.upper()}", "").strip()
                   or "UNOBSERVED" for key in PHASES}
        payload = {"schema_version": 1, "run_id": int(os.environ["GITHUB_RUN_ID"]),
                   "source_sha": os.environ["CANDIDATE_SHA"],
                   "observed_at_utc": datetime.now(timezone.utc).isoformat(),
                   "outcomes": outcome}
        receipt = snapshot(payload, rca._json_file(args.registry, rca.MAX_REGISTRY_BYTES))
        _write(args.output, receipt)
    except (rca.UnsafeRCAInput, OSError, KeyError, TypeError, ValueError):
        print("RC6_CI_RCA_SNAPSHOT=RED|no_go=YES", file=sys.stderr)
        return 2
    print(f"RC6_CI_RCA_SNAPSHOT=DIAGNOSTIC_ONLY|failed_phases={receipt['phase_failures']}|"
          f"coverage={receipt['coverage']}|no_go=YES")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
