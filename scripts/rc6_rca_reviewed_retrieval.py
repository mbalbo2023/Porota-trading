"""A212 offline feedback retrieval for *reviewed* RCA, with strict abstention.

This is a deterministic knowledge-base update, NOT a trained ML model or a
root-cause classifier. A reviewed label is supplied as curated structured
evidence; its GitHub comment is NOT verified by this offline module. A single
failure, a matching error code, a phase-only UNKNOWN, or a majority vote can
never certify an RCA. Nothing here changes Actions, runtime, PAPER or GO.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import tempfile

from scripts import rc6_rca_learning as rca


REVIEW_FIELDS = frozenset({"event", "review_comment_id", "reviewed_at_utc",
                           "rca_id", "fix_commit_sha", "guard_path",
                           "test_path", "evidence_state"})
ELIGIBLE_STATES = frozenset({"ARTEFACTO_VALIDADO", "VALIDADO_RUNTIME"})
MAX_REVIEWS = 3000
MAX_FEEDBACK_BYTES = 512 * 1024
MIN_DISTINCT_REVIEWED_RUNS = 2
DEFAULT_FEEDBACK = (Path(__file__).resolve().parents[1] /
                    "ops/policy/rc6-rca-reviewed-incidents-v1.json")


def _instant(utc_text: str) -> datetime:
    # Lexical ISO strings misorder whole vs fractional seconds (".1Z" < "Z").
    return datetime.fromisoformat(utc_text.replace("Z", "+00:00"))


def _feedback(data: object) -> dict[str, list[dict]]:
    if not isinstance(data, dict) or set(data) != {"schema_version", "reviews"}:
        rca._reject()
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        rca._reject()
    entries = data["reviews"]
    if not isinstance(entries, list) or len(entries) > MAX_REVIEWS:
        rca._reject()
    indexed = defaultdict(list)
    seen_comment_ids = set()
    for record in entries:
        if not isinstance(record, dict) or set(record) != REVIEW_FIELDS:
            rca._reject()
        signature, observed = rca._event(record["event"])
        # A review with no immutable incident evidence cannot become support.
        if observed["evidence_sha256"] is None:
            rca._reject()
        comment_id = record["review_comment_id"]
        if (type(comment_id) is not int or not 0 < comment_id < 10**17 or
                comment_id in seen_comment_ids):
            rca._reject()
        seen_comment_ids.add(comment_id)
        reviewed_utc, _ = rca._utc(record["reviewed_at_utc"])
        if _instant(reviewed_utc) < _instant(observed["timestamp_utc"]):
            rca._reject()
        state = record["evidence_state"]
        if not isinstance(state, str) or state not in rca.STATES:
            rca._reject()
        mapping = {"rca_id": rca._checked(record["rca_id"],
                                          r"RC6-[A-Z0-9_-]{2,64}"),
                   "fix_commit_sha": rca._hex(record["fix_commit_sha"], 40),
                   "guard_path": rca._repo_path(record["guard_path"]),
                   "test_path": rca._repo_path(record["test_path"])}
        indexed[signature["fingerprint"]].append({
            "mapping": mapping, "incident_run_id": observed["run_id"],
            "review_comment_id": comment_id, "reviewed_at_utc": reviewed_utc,
            "evidence_state": state})
    return dict(indexed)


def retrieve(incidents: object, feedback: object) -> dict:
    """Suggest exact reviewed repeats only; abstain on any causal ambiguity.

    Use only reviews published BEFORE the earliest query failure and from
    different incident runs (point-in-time holdout), preventing label leakage.
    An input comment ID is a reference, not a verified signature or authority.
    """
    base = rca.analyze(incidents)
    reviews = _feedback(feedback)
    results = []
    retrieved = 0
    for group in base["groups"]:
        fingerprint = group["fingerprint"]
        query_runs = {row["run_id"] for row in group["observations"]}
        first_event = min(_instant(row["timestamp_utc"])
                          for row in group["observations"])
        history = [row for row in reviews.get(fingerprint, ())
                   if (_instant(row["reviewed_at_utc"]) < first_event and
                       row["incident_run_id"] not in query_runs)]
        verified = [row for row in history
                    if row["evidence_state"] in ELIGIBLE_STATES]
        distinct_runs = len({row["incident_run_id"] for row in verified})
        mappings = {json.dumps(row["mapping"], sort_keys=True,
                               separators=(",", ":")) for row in history}
        if group["dimensions"]["error_code"] == "UNKNOWN":
            reason = "UNKNOWN_ERROR_CODE"
        elif not history:
            reason = "NO_PIT_REVIEWED_HISTORY"
        elif len(mappings) != 1:
            reason = "CONFLICTED_REVIEWED_MAPPING"
        elif distinct_runs < MIN_DISTINCT_REVIEWED_RUNS:
            reason = "INSUFFICIENT_VERIFIED_DISTINCT_RUNS"
        else:
            reason = "REVIEWED_EXACT_RETRIEVAL_REVIEW_REQUIRED"
        match = verified[0]["mapping"] if reason.startswith("REVIEWED_EXACT_") else None
        retrieved += match is not None
        results.append({"fingerprint": fingerprint,
                        "dimensions": group["dimensions"],
                        "status": reason,
                        "suggested_reviewed_mapping": match,
                        "reviewed_history_count": len(history),
                        "verified_history_count": len(verified),
                        "verified_distinct_runs": distinct_runs,
                        "causal_certainty": "NOT_ESTABLISHED",
                        "root_cause_confirmed_by_analyzer": False})
    return {"schema_version": 1,
            "mode": "DETERMINISTIC_REVIEWED_FEEDBACK_RETRIEVAL",
            "model_trained": False,
            "statistical_calibration": "NOT_AVAILABLE",
            "external_comment_attestation_verified": False,
            "go_decision": "NO_AUTOMATIC_GO",
            "auto_merge": False, "auto_deploy": False, "auto_trade": False,
            "input_failures": base["input_failures"],
            "distinct_signatures": base["distinct_signatures"],
            "retrieved_signatures_needing_human_review": retrieved,
            "results": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--incidents", required=True, type=Path)
    parser.add_argument("--feedback", type=Path, default=DEFAULT_FEEDBACK)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    temp_path = None
    try:
        result = retrieve(rca._json_file(args.incidents, rca.MAX_INCIDENT_BYTES),
                          rca._json_file(args.feedback, MAX_FEEDBACK_BYTES))
        target = args.output
        if target.is_symlink() or (target.exists() and not target.is_file()):
            rca._reject()
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=target.parent, prefix=".rc6-rca-feedback-",
                                         delete=False) as handle:
            temp_path = Path(handle.name)
            os.chmod(temp_path, 0o600)
            json.dump(result, handle, sort_keys=True, indent=2)
            handle.write("\n")
        os.replace(temp_path, target)
    except (OSError, ValueError, TypeError, rca.UnsafeRCAInput):
        print("RC6_RCA_FEEDBACK=RED|automatic_go=NO", file=sys.stderr)
        return 2
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
    print("RC6_RCA_FEEDBACK=DIAGNOSTIC_ONLY|matches=" +
          str(result["retrieved_signatures_needing_human_review"]) +
          "|automatic_go=NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
