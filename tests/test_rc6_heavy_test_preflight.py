import json
from pathlib import Path
import unittest

from scripts.rc6_heavy_test_preflight import evaluate_capacity, load_policy


class HeavyTestPreflightTests(unittest.TestCase):
    def test_capacity_green_at_exact_required_bytes(self) -> None:
        result = evaluate_capacity(
            free_bytes=14,
            total_inodes=100,
            free_inodes=10,
            expected_peak_bytes=10,
            residual_reserve_bytes=4,
            minimum_free_inode_ratio=0.10,
        )

        self.assertEqual(result["status"], "GREEN")
        self.assertEqual(result["blockers"], [])
        self.assertEqual(result["required_free_bytes"], 14)

    def test_capacity_blocks_one_byte_below_required(self) -> None:
        result = evaluate_capacity(
            free_bytes=13,
            total_inodes=100,
            free_inodes=100,
            expected_peak_bytes=10,
            residual_reserve_bytes=4,
            minimum_free_inode_ratio=0.10,
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["blockers"], ["CAPACITY_BYTES_INSUFFICIENT"])

    def test_capacity_blocks_low_inode_reserve(self) -> None:
        result = evaluate_capacity(
            free_bytes=100,
            total_inodes=100,
            free_inodes=9,
            expected_peak_bytes=10,
            residual_reserve_bytes=4,
            minimum_free_inode_ratio=0.10,
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["blockers"], ["CAPACITY_INODES_INSUFFICIENT"])

    def test_capacity_rejects_unknown_or_zero_peak(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive measured value"):
            evaluate_capacity(
                free_bytes=100,
                total_inodes=100,
                free_inodes=100,
                expected_peak_bytes=0,
                residual_reserve_bytes=4,
                minimum_free_inode_ratio=0.10,
            )

    def test_repository_policy_preserves_original_big_limits(self) -> None:
        policy_path = (
            Path(__file__).resolve().parents[1]
            / "ops/policy/rc6-heavy-test-governance-v1.json"
        )
        policy = load_policy(policy_path)

        self.assertEqual(
            policy["performance"],
            {
                "big_workload_catalog_instruments": 12000,
                "big_workload_observations": 60000,
                "hard_ceiling_seconds": 90,
                "qualification_target_seconds": 75,
                "maximum_rss_bytes": 2147483648,
                "maximum_evidence_bytes": 134217728,
                "maximum_retained_entries": 100000,
                "workload_assertions_or_limits_may_be_relaxed": False,
                "same_sha_focals_before_full_predeploy": True,
                "full_predeploy_on_missing_red_or_pending_focal_receipt": "BLOCK",
                "isolated_pass_above_qualification_target": "DIAGNOSTIC_ONLY",
            },
        )

        rendered = json.dumps(policy, sort_keys=True)
        self.assertIn("PRODUCTION_PAPER / SIMULATION", rendered)
        self.assertEqual(policy["safety"]["real_orders_sent"], 0)


if __name__ == "__main__":
    unittest.main()
