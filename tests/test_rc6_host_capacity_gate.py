import unittest

from scripts.rc6_host_capacity_gate import screen


class HostCapacityGateTests(unittest.TestCase):
    def setUp(self):
        self.host = {
            "schema": "rc6.host-capacity-readonly.v1",
            "observed_utc": "2026-10-07T17:04:04+00:00",
            "memory": {"MemTotal_bytes": 1008185344,
                       "MemAvailable_bytes": 135340032},
            "cpu_affinity_count": 1,
        }

    def test_actual_host_snapshot_blocks_observed_big_peak(self):
        result = screen(self.host, peak_rss_bytes=1830219776,
                        qualified_cpu_count=4, reserve_bytes=256 * 1024 * 1024)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("MEASURED_PEAK_AND_RESERVE_EXCEED_HOST_PHYSICAL_RAM", result["reasons"])
        self.assertIn("CANDIDATE_NOT_QUALIFIED_AT_HOST_CPU_COUNT", result["reasons"])
        self.assertFalse(result["deploy_approved"])

    def test_missing_or_invalid_measurements_fail_closed(self):
        for snapshot in (None, {}, {**self.host, "cpu_affinity_count": 0},
                         {**self.host, "memory": {"MemTotal_bytes": 1000}}):
            with self.subTest(snapshot=snapshot):
                self.assertEqual(screen(snapshot, peak_rss_bytes=1,
                                        qualified_cpu_count=1, reserve_bytes=0)["status"], "BLOCKED")

    def test_sufficient_snapshot_is_still_not_deploy_approval(self):
        host = {**self.host, "cpu_affinity_count": 4,
                "memory": {"MemTotal_bytes": 8 * 1024**3,
                           "MemAvailable_bytes": 5 * 1024**3}}
        result = screen(host, peak_rss_bytes=1024**3,
                        qualified_cpu_count=4, reserve_bytes=256 * 1024**2)
        self.assertEqual(result["status"], "SNAPSHOT_SCREEN_PASSED_NOT_DEPLOY_APPROVAL")
        self.assertFalse(result["deploy_approved"])


if __name__ == "__main__":
    unittest.main()
