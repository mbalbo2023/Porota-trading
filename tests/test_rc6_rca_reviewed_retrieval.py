"""A238/A212: reviewed RCA feedback is retrieval, never trained ML or GO."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from scripts import rc6_rca_learning as rca
from scripts import rc6_rca_reviewed_retrieval as feedback


SHA = "c75d971d541ef7658ea12f844b9476fbeed239da"


def incident(run_id=100, timestamp_utc="2026-11-01T12:00:00Z", **changed):
    row = {"component": "ci_tests", "failure_phase": "CI_PARALLEL_PILOT",
           "error_code": "SHARD_NODEID_UTF8_RED", "contract_version": "A236-v1",
           "family": "UNSPECIFIED", "process_state": "UNSPECIFIED",
           "schema_identity": "rc6.pytest.identity.v1", "run_id": run_id,
           "source_sha": SHA, "timestamp_utc": timestamp_utc,
           "test_nodeid": "tests/test_safe.py::test_label[á]",
           "evidence_sha256": "f" * 64}
    row.update(changed)
    return row


def reviewed(row, comment_id, reviewed_at_utc, **changed):
    entry = {"event": row, "review_comment_id": comment_id,
             "reviewed_at_utc": reviewed_at_utc,
             "rca_id": "RC6-A236-UTF8", "fix_commit_sha": "a" * 40,
             "guard_path": "scripts/rc6_rca_learning.py",
             "test_path": "tests/test_rc6_rca_learning.py",
             "evidence_state": "ARTEFACTO_VALIDADO"}
    entry.update(changed)
    return entry


def reviews(*entries):
    return {"schema_version": 1, "reviews": list(entries)}


def incidents(*entries):
    return {"schema_version": 1, "events": list(entries)}


def history():
    a = reviewed(incident(100, "2026-10-27T12:00:00Z"), 6547853639,
                 "2026-10-27T17:00:00Z")
    b = reviewed(incident(101, "2026-10-28T12:00:00Z"), 6547916408,
                 "2026-10-28T17:00:00Z")
    # These are SYNTHETIC review fixtures; actual policy stays EMPTY.
    return a, b


class TestReviewedRCARetrieval(unittest.TestCase):
    def test_default_registry_empty_and_phase_only_unknown_abstain(self):
        policy = json.loads(feedback.DEFAULT_FEEDBACK.read_text(encoding="utf-8"))
        self.assertEqual(policy, {"schema_version": 1, "reviews": []})
        result = feedback.retrieve(incidents(incident()), policy)
        self.assertEqual(result["retrieved_signatures_needing_human_review"], 0)
        self.assertEqual(result["results"][0]["status"], "NO_PIT_REVIEWED_HISTORY")
        self.assertFalse(result["model_trained"])
        self.assertEqual(result["statistical_calibration"], "NOT_AVAILABLE")
        self.assertFalse(result["external_comment_attestation_verified"])
        self.assertEqual(result["go_decision"], "NO_AUTOMATIC_GO")
        for field in ("auto_merge", "auto_deploy", "auto_trade"):
            self.assertFalse(result[field])
        unknown = feedback.retrieve(incidents(incident(error_code="UNKNOWN")), policy)
        self.assertEqual(unknown["results"][0]["status"], "UNKNOWN_ERROR_CODE")

    def test_two_distinct_historical_reviewed_runs_retrieve_not_confirm(self):
        a, b = history()
        query = incidents(incident(999, "2026-11-01T12:00:00Z", source_sha="b"*40))
        result = feedback.retrieve(query, reviews(a, b))
        self.assertEqual(result["retrieved_signatures_needing_human_review"], 1)
        match = result["results"][0]
        self.assertEqual(match["status"], "REVIEWED_EXACT_RETRIEVAL_REVIEW_REQUIRED")
        self.assertEqual(match["verified_distinct_runs"], 2)
        self.assertEqual(match["suggested_reviewed_mapping"]["rca_id"],
                         "RC6-A236-UTF8")
        self.assertFalse(match["root_cause_confirmed_by_analyzer"])
        self.assertEqual(feedback.retrieve(query, reviews(b, a)), result)
        self.assertNotIn("test_safe.py", json.dumps(result))

    def test_one_review_or_two_comments_one_run_cannot_masquerade_as_learning(self):
        a, b = history()
        query = incidents(incident(999))
        self.assertEqual(feedback.retrieve(query, reviews(a))["results"][0]["status"],
                         "INSUFFICIENT_VERIFIED_DISTINCT_RUNS")
        b["event"]["run_id"] = a["event"]["run_id"]
        self.assertEqual(feedback.retrieve(query, reviews(a, b))["results"][0]["status"],
                         "INSUFFICIENT_VERIFIED_DISTINCT_RUNS")

    def test_conflicting_reviewer_labels_and_fix_mapping_abstain(self):
        a, b = history()
        query = incidents(incident(999))
        for changed in ({"rca_id": "RC6-A237-DIFFERENT"},
                        {"fix_commit_sha": "b" * 40},
                        {"guard_path": "scripts/rc6_ci_rca_snapshot.py"}):
            with self.subTest(changed=changed):
                second = {**b, **changed}
                result = feedback.retrieve(query, reviews(a, second))
                self.assertEqual(result["results"][0]["status"],
                                 "CONFLICTED_REVIEWED_MAPPING")
                self.assertIsNone(result["results"][0]["suggested_reviewed_mapping"])

    def test_unknown_root_cause_never_learned_from_two_human_labels(self):
        a, b = history()
        for entry in (a, b):
            entry["event"]["error_code"] = "UNKNOWN"
        query = incidents(incident(error_code="UNKNOWN"))
        result = feedback.retrieve(query, reviews(a, b))
        self.assertEqual(result["results"][0]["status"], "UNKNOWN_ERROR_CODE")
        self.assertEqual(result["retrieved_signatures_needing_human_review"], 0)

    def test_pit_future_review_and_same_run_holdout_not_used(self):
        a, b = history()
        query = incidents(incident(run_id=100, timestamp_utc="2026-10-28T13:00:00Z"))
        result = feedback.retrieve(query, reviews(a, b))
        self.assertEqual(result["results"][0]["status"], "NO_PIT_REVIEWED_HISTORY")
        late = incidents(incident(run_id=999, timestamp_utc="2026-11-01T12:00:00Z"))
        b["reviewed_at_utc"] = "2026-11-02T12:00:00Z"
        result = feedback.retrieve(late, reviews(a, b))
        self.assertEqual(result["results"][0]["status"],
                         "INSUFFICIENT_VERIFIED_DISTINCT_RUNS")

    def test_fractional_seconds_do_not_leak_future_reviews_into_pit(self):
        a, b = history()
        # 12:00:00.1 is after 12:00:00, despite its ISO string sorting first.
        b["reviewed_at_utc"] = "2026-11-01T12:00:00.100Z"
        query = incidents(incident(999, "2026-11-01T12:00:00Z"))
        outcome = feedback.retrieve(query, reviews(a, b))
        self.assertEqual(outcome["results"][0]["status"],
                         "INSUFFICIENT_VERIFIED_DISTINCT_RUNS")
        # A reviewed label cannot precede the incident even by 0.1s.
        b["event"]["timestamp_utc"] = "2026-11-01T12:00:00.200Z"
        with self.assertRaises(rca.UnsafeRCAInput):
            feedback.retrieve(query, reviews(a, b))

    def test_no_review_causal_authority_from_insufficient_evidence_state(self):
        a, b = history()
        b["evidence_state"] = "PROPUESTO"
        result = feedback.retrieve(incidents(incident(999)), reviews(a, b))
        self.assertEqual(result["results"][0]["status"],
                         "INSUFFICIENT_VERIFIED_DISTINCT_RUNS")
        self.assertEqual(result["results"][0]["verified_history_count"], 1)

    def test_duplication_raw_logs_bad_sha_path_and_time_are_rejected(self):
        a, b = history()
        bad = (
            reviews(a, a),
            reviews({**a, "review_comment_id": True}),
            reviews({**a, "reviewed_at_utc": "2026-10-26T15:00:00Z"}),
            reviews({**a, "fix_commit_sha": "x"*40}),
            reviews({**a, "test_path": "../private/file"}),
            reviews({**a, "event": {**a["event"], "raw_log": "secret-NEVER-COPY"}}),
            reviews({**a, "event": {**a["event"], "evidence_sha256": None}}),
            {"schema_version": 1, "reviews": [a], "extra": "SECRET"},
        )
        for case in bad:
            with self.subTest(case=json.dumps(case,default=str)[:55]):
                with self.assertRaises(rca.UnsafeRCAInput):
                    feedback.retrieve(incidents(incident()), case)

    def test_cli_sanitized_output_and_symlink_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root/"incidents.json", root/"retrieved.json"
            source.write_text(json.dumps(incidents(incident())),encoding="utf-8")
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                self.assertEqual(feedback.main(["--incidents", str(source),
                                                "--output", str(target)]), 0)
            self.assertIn("automatic_go=NO",stream.getvalue())
            saved=json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(saved["retrieved_signatures_needing_human_review"],0)
            self.assertEqual(target.stat().st_mode & 0o777,0o600)
            target.unlink()
            victim=root/"victim.json"
            victim.write_text("DO_NOT_REPLACE",encoding="utf-8")
            target.symlink_to(victim)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(feedback.main(["--incidents", str(source),
                                                "--output", str(target)]), 2)
            self.assertEqual(victim.read_text(encoding="utf-8"),"DO_NOT_REPLACE")


if __name__ == "__main__":
    unittest.main()
