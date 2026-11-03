"""A222: no-host RCA signature, deduplication and leak-prevention contract."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import rc6_rca_learning as rca


def event(**overrides):
    value = {
        "component": "deploy_quiescence",
        "error_code": "QUIESCENCE_POSTSTART_SESSION_UNVERIFIED",
        "contract_version": "a219-v1",
        "failure_phase": "DEPLOY_POSTSTART",
        "family": "CAUCIONES",
        "process_state": "DEGRADED",
        "schema_identity": "observer-v17",
        "run_id": 41208586626,
        "source_sha": "c75d971d541ef7658ea12f844b9476fbeed239da",
        "timestamp_utc": "2026-10-31T14:22:03Z",
        "test_nodeid": "tests/test_rc6_deploy_quiescence.py::test_poststart",
        "evidence_sha256": "f" * 64,
    }
    value.update(overrides)
    return value


def report(*events):
    return {"schema_version": 1, "events": list(events)}


class TestRCALearning(unittest.TestCase):
    def test_repeated_tests_are_one_signature_but_not_one_confirmed_cause(self):
        one, two = event(), event(test_nodeid="tests/test_other.py::test_other")
        result = rca.analyze(report(one, two))
        self.assertEqual(result["input_failures"], 2)
        self.assertEqual(result["distinct_signatures"], 1)
        self.assertEqual(result["groups"][0]["reported_failures"], 2)
        self.assertEqual(result["groups"][0]["unique_reports"], 2)
        self.assertEqual(result["groups"][0]["classification"], "UNKNOWN")
        self.assertEqual(result["confirmed_rca_count"], 0)
        self.assertFalse(result["groups"][0]["root_cause_confirmed_by_analyzer"])

    def test_dedup_identical_observation_is_idempotent(self):
        value = event()
        result = rca.analyze(report(value, dict(value)))
        self.assertEqual(result["input_failures"], 2)
        self.assertEqual(result["unique_reports"], 1)
        self.assertEqual(result["groups"][0]["reported_failures"], 2)

    def test_grouping_independent_of_order_and_source_sha(self):
        a = event()
        b = event(run_id=42000000000, source_sha="a" * 40,
                  timestamp_utc="2026-11-01T00:00:00Z")
        self.assertEqual(rca.analyze(report(a, b)), rca.analyze(report(b, a)))
        self.assertEqual(rca.analyze(report(a, b))["distinct_signatures"], 1)
        self.assertEqual(rca.analyze(report(a, b))["groups"][0]["different_runs"], 2)

    def test_semantic_dimensions_never_coalesce(self):
        base = event()
        variants = [event(family="FUTUROS"), event(process_state="STARTING"),
                    event(schema_identity="observer-v18"),
                    event(contract_version="a220-v2"),
                    event(error_code="QUIESCENCE_PAPER_REQUIRED"),
                    event(failure_phase="PREDEPLOY_HOST_REPLAY")]
        result = rca.analyze(report(base, *variants))
        self.assertEqual(result["distinct_signatures"], len(variants) + 1)

    def test_registry_requires_exact_signature_and_never_decides_go(self):
        fp = rca.analyze(report(event()))["groups"][0]["fingerprint"]
        registry = {"schema_version": 1, "signatures": [{
            "fingerprint": fp,
            "rca_id": "RC6-A218-POSTSTART",
            "fix_commit_sha": "c75d971d541ef7658ea12f844b9476fbeed239da",
            "guard_path": "scripts/rc6_deploy_quiescence.py",
            "test_path": "tests/test_rc6_deploy_quiescence.py",
            "evidence_state": "ARTEFACTO_VALIDADO",
        }]}
        result = rca.analyze(report(event()), registry)
        group = result["groups"][0]
        self.assertTrue(group["recurrent_known_signature"])
        self.assertEqual(group["classification"], "REGISTRY_MATCH_REVIEW_REQUIRED")
        self.assertEqual(group["registry_rca"]["test_path"],
                         "tests/test_rc6_deploy_quiescence.py")
        self.assertEqual(result["go_decision"], "NO_AUTOMATIC_GO")
        self.assertFalse(result["auto_merge"] or result["auto_deploy"] or result["auto_trade"])

    def test_unknown_error_code_remains_unknown_even_if_registry_matches(self):
        unknown = event(error_code="UNKNOWN")
        fp = rca.analyze(report(unknown))["groups"][0]["fingerprint"]
        registry = {"schema_version": 1, "signatures": [{
            "fingerprint": fp, "rca_id": "RC6-UNKNOWN",
            "fix_commit_sha": "a" * 40,
            "guard_path": "scripts/rc6_rca_learning.py",
            "test_path": "tests/test_rc6_rca_learning.py",
            "evidence_state": "EN_GITHUB"}]}
        self.assertEqual(rca.analyze(report(unknown), registry)["groups"][0]
                         ["classification"], "UNKNOWN")

    def test_missing_code_is_not_fabricated(self):
        unknown = event()
        unknown.pop("error_code")
        result = rca.analyze(report(unknown))
        self.assertEqual(result["groups"][0]["dimensions"]["error_code"], "UNKNOWN")
        self.assertEqual(result["groups"][0]["classification"], "UNKNOWN")

    def test_chronology_utc_and_art_explicit(self):
        obs = rca.analyze(report(event()))["groups"][0]["observations"][0]
        self.assertEqual(obs["timestamp_utc"], "2026-10-31T14:22:03Z")
        self.assertEqual(obs["timestamp_art"], "2026-10-31T11:22:03-03:00")

    def test_sensitive_test_name_only_sha256_in_output(self):
        secret = "private-account-NEVER-EMIT"
        result = rca.analyze(report(event(test_nodeid=f"tests/test_x.py::test[{secret}]")))
        rendered = json.dumps(result)
        self.assertNotIn(secret, rendered)
        self.assertIn("test_nodeid_sha256", rendered)

    def test_text_logs_unknown_fields_and_secret_keys_fail_closed(self):
        for name in ("message", "raw_log", "stacktrace", "Authorization", "token", "account_id"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(rca.UnsafeRCAInput, "RC6_RCA_INPUT_REJECTED"):
                    rca.analyze(report(event(**{name: "sensitive-value"})))

    def test_bad_clock_run_sha_schema_and_code_rejected(self):
        bad = [event(timestamp_utc="2026-10-31T11:22:03-03:00"),
               event(timestamp_utc="2026-10-31T14:22:03"),
               event(run_id=True), event(source_sha="a" * 39),
               event(error_code="untrusted text"), event(test_nodeid="x" * 2049)]
        for value in bad:
            with self.assertRaises(rca.UnsafeRCAInput):
                rca.analyze(report(value))
        with self.assertRaises(rca.UnsafeRCAInput):
            rca.analyze({"schema_version": "1", "events": []})

    def test_registry_rejects_duplicate_and_traversal_paths(self):
        fp = rca.analyze(report(event()))["groups"][0]["fingerprint"]
        record = {"fingerprint": fp, "rca_id": "RC6-A219-POSTSTART",
                  "fix_commit_sha": "a" * 40, "guard_path": "../secrets",
                  "test_path": "tests/test_rc6_rca_learning.py",
                  "evidence_state": "EN_GITHUB"}
        with self.assertRaises(rca.UnsafeRCAInput):
            rca.analyze(report(event()), {"schema_version": 1, "signatures": [record]})
        record["guard_path"] = "scripts/rc6_rca_learning.py"
        with self.assertRaises(rca.UnsafeRCAInput):
            rca.analyze(report(event()), {"schema_version": 1,
                                          "signatures": [record, dict(record)]})

    def test_malformed_registry_states_unknown_paths_and_symlinks_fail_closed(self):
        fp = rca.analyze(report(event()))["groups"][0]["fingerprint"]
        entry = {"fingerprint": fp, "rca_id": "RC6-A219-POSTSTART",
                 "fix_commit_sha": "a" * 40, "guard_path": "scripts/guard.py",
                 "test_path": "tests/test_guard.py", "evidence_state": "EN_GITHUB"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            (root / "tests").mkdir()
            (root / "scripts/guard.py").write_text("safe", encoding="utf-8")
            (root / "tests/test_guard.py").write_text("safe", encoding="utf-8")
            with patch.object(rca, "REGISTRY_ROOT", root):
                data = {"schema_version": 1, "signatures": [entry]}
                self.assertEqual(rca.analyze(report(event()), data)["groups"][0]
                                 ["classification"], "REGISTRY_MATCH_REVIEW_REQUIRED")
                for malformed in ([], {}, 0, None, True, "UNKNOWN"):
                    with self.subTest(malformed=malformed):
                        bad = {**entry, "evidence_state": malformed}
                        with self.assertRaises(rca.UnsafeRCAInput):
                            rca.analyze(report(event()), {"schema_version": 1,
                                                          "signatures": [bad]})
                with self.assertRaises(rca.UnsafeRCAInput):
                    rca.analyze(report(event()), {"schema_version": 1,
                                                 "signatures": [{**entry, "test_path":
                                                                 "tests/nonexistent.py"}]})
                (root / "tests/link.py").symlink_to(root / "tests/test_guard.py")
                with self.assertRaises(rca.UnsafeRCAInput):
                    rca.analyze(report(event()), {"schema_version": 1,
                                                 "signatures": [{**entry, "test_path":
                                                                 "tests/link.py"}]})
                (root / "linked").symlink_to(root / "tests", target_is_directory=True)
                with self.assertRaises(rca.UnsafeRCAInput):
                    rca.analyze(report(event()), {"schema_version": 1,
                                                 "signatures": [{**entry, "test_path":
                                                                 "linked/test_guard.py"}]})

    def test_unencodable_nodeid_is_rejected_without_trace_or_leak(self):
        with self.assertRaisesRegex(rca.UnsafeRCAInput, "RC6_RCA_INPUT_REJECTED"):
            rca.analyze(report(event(test_nodeid="bad\ud800nodeid")))

    def test_cli_writes_only_sanitized_output_and_stays_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "source.json", root / "rca-incident.json"
            secret = "private-NEVER-LOG"
            source.write_text(json.dumps(report(event(test_nodeid=secret))), encoding="utf-8")
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = rca.main(["--input", str(source), "--output", str(output)])
            self.assertEqual(code, 0)
            self.assertIn("automatic_go=NO", stream.getvalue())
            self.assertNotIn(secret, output.read_text(encoding="utf-8"))
            self.assertEqual(json.loads(output.read_text())["analysis_mode"], "READ_ONLY")
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_cli_does_not_echo_secret_or_overwrite_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output, victim = root / "source.json", root / "result.json", root / "safe.txt"
            secret = "top_secret_to_never_echo"
            source.write_text(json.dumps(report(event(**{"raw_log": secret}))), encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(rca.main(["--input", str(source), "--output", str(output)]), 2)
            self.assertNotIn(secret, stderr.getvalue())
            self.assertFalse(output.exists())
            source.write_text(json.dumps(report(event())), encoding="utf-8")
            victim.write_text("UNTOUCHED", encoding="utf-8")
            output.symlink_to(victim)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(rca.main(["--input", str(source), "--output", str(output)]), 2)
            self.assertEqual(victim.read_text(), "UNTOUCHED")


if __name__ == "__main__":
    unittest.main()
