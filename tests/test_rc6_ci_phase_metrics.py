"""Offline, no-secret/non-authoritative Actions step-timing examples."""

import unittest

from scripts import rc6_ci_phase_metrics as timings


SHA = "c75d971d541ef7658ea12f844b9476fbeed239da"


def step(name, start="2026-10-31T15:03:10Z", end="2026-10-31T15:07:04Z",
         conclusion="success", status="completed"):
    return {"name": name, "status": status, "conclusion": conclusion,
            "started_at": start, "completed_at": end}


def payload(*steps):
    return {"schema_version": 1, "run_id": 41212303000,
            "source_sha": SHA, "steps": list(steps)}


class TestPhaseMetrics(unittest.TestCase):
    def test_build_duration_and_art_localization(self):
        report = timings.analyze(payload(step("Build candidate exactly once")))
        phase = report["phases"]["DOCKER_BUILD_ONCE"]
        self.assertEqual(phase["elapsed_seconds"], 234.0)
        self.assertEqual(phase["started_at_art"], "2026-10-31T12:03:10-03:00")
        self.assertEqual(report["timing_status"], "PARTIAL_PHASE_SAMPLE")
        self.assertFalse(report["automatic_go"])

    def test_all_five_phases_measured(self):
        report = timings.analyze(payload(*(step(name) for name in timings.PHASES)))
        self.assertEqual(report["phase_count_measured"], 5)
        self.assertEqual(report["observed_phase_seconds_sum"], 1170.0)
        self.assertEqual(report["timing_status"], "COMPLETE_PHASE_SAMPLE")

    def test_unknown_step_label_not_leaked(self):
        secret = "SecretAccount:NEVER_EMIT"
        report = timings.analyze(payload(step("Build candidate exactly once"), step(secret)))
        self.assertEqual(report["unknown_step_count"], 1)
        self.assertNotIn(secret, str(report))

    def test_missing_and_inflight_are_no_verificado(self):
        report = timings.analyze(payload(step("Upload predeploy evidence",
                                              end=None, status="in_progress",
                                              conclusion=None)))
        self.assertEqual(report["phases"]["ARTIFACT_UPLOAD"]["state"], "NO_VERIFICADO")

    def test_failures_remain_visible_not_greenwash(self):
        report = timings.analyze(payload(step("Governed automatic test discovery and execution",
                                              conclusion="failure")))
        self.assertEqual(report["phases"]["GOVERNED_TESTS"]["conclusion"], "failure")
        self.assertFalse(report["automatic_go"])

    def test_reject_bad_time_status_untrusted_fields_and_duplicates(self):
        malformed = [step("Build candidate exactly once", end="2026-10-31T15:00:00Z"),
                     step("Build candidate exactly once", start="2026-10-31T12:03:10-03:00"),
                     step("Build candidate exactly once", status="GO"),
                     step("Build candidate exactly once") | {"raw_log": "private"}]
        for value in malformed:
            with self.assertRaises(timings.PhaseMetricsInvalid):
                timings.analyze(payload(value))
        with self.assertRaises(timings.PhaseMetricsInvalid):
            timings.analyze(payload(step("Build candidate exactly once"),
                                    step("Build candidate exactly once")))

    def test_invalid_source_sha_cannot_reuse_timing_receipt(self):
        report = payload(step("Build candidate exactly once"))
        report["source_sha"] = "a" * 39
        with self.assertRaises(timings.PhaseMetricsInvalid):
            timings.analyze(report)


if __name__ == "__main__":
    unittest.main()
