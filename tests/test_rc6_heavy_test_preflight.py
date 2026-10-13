"""Capacity admission negatives; no heavy producer or runtime qualification."""
import copy
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from scripts import rc6_heavy_test_preflight as preflight


def binding():
    return {"candidate_sha": "a" * 40, "candidate_tree": "b" * 40, "producer": "controlled-capacity-test",
            "attempt_id": "controlled-1", "owner_id": "controlled-owner", "runner_class": "DIAGNOSTIC",
            "workload_fingerprint": "c" * 64}


def comparable_peak(value=1024**3):
    b = binding()
    measurement = {"allocated_bytes": value, "retained_entries": 12,
                   "assertion_scope": "CONTROLLED_UNIT_FIXTURE_ONLY"}
    return {"schema": "porota.rc6.comparable-capacity-peak.v1",
            **{k: b[k] for k in ("producer", "runner_class", "workload_fingerprint")},
            "peak_allocated_bytes": value, "evidence": {
                "uri": "https://github.com/mbalbo2023/Porota-trading/issues/471",
                "measurement": measurement, "sha256": preflight.digest(measurement)}}


def comparable_envelope(bound=None):
    """A synthetic DIAGNOSTIC comparison exercises validation, never promotion."""
    bound = binding() if bound is None else bound
    scope = "OWN_CLOSED_RETAINED_NAMESPACE_OR_OBSERVED_COMPONENTS"
    measurement = {"allocated_bytes": 1024**3, "retained_entries": 226749,
                   "origin_runner_class": "DIAGNOSTIC", "scope": scope,
                   "assertion_scope": "CONTROLLED_UNIT_FIXTURE_ONLY"}
    uri = "https://github.com/mbalbo2023/Porota-trading/issues/471"
    graph_hash = "e" * 64
    return {"schema": preflight.ENVELOPE_SCHEMA,
            "target": {name: bound[name] for name in ("producer", "runner_class", "workload_fingerprint")},
            "reference": {"measurement_kind": "OBSERVED_ALLOCATED_HIGH_WATER", "measurement_scope": scope,
                "measurement_complete": True, "runner_class": "DIAGNOSTIC", "producer": "controlled-reference",
                "source_sha": "d" * 40, "source_tree": "e" * 40,
                "allocated_bytes": measurement["allocated_bytes"], "retained_entries": measurement["retained_entries"],
                "evidence": {"uri": uri, "raw_member": "controlled-fixture/allocation.json",
                    "raw_member_sha256": "f" * 64, "container_sha256": "0" * 64, "measurement": measurement,
                    "measurement_sha256": preflight.digest(measurement)}},
            "comparison": {"schema": "porota.rc6.capacity-comparison-proof.v1",
                "candidate_sha": bound["candidate_sha"], "candidate_tree": bound["candidate_tree"],
                "source_manifest_sha256": "f" * 64, "cheap_files_sha256": "d" * 64,
                "producer_graph_sha256": graph_hash,
                "reference_graph_evidence": {"uri": uri, "sha256": "a" * 64},
                "target_graph_evidence": {"uri": uri, "sha256": graph_hash},
                "dominance": "REFERENCE_GRAPH_PLUS_ENUMERATED_BOUNDED_ADDITIONS",
                "checks": {name: True for name in preflight.COMPARISON_CHECKS}, "unknown_components": [],
                "additional_bound_components": [{"name": "controlled-extra-payload", "quantity": 3,
                    "unit_bound_bytes": 4096, "allocated_bound_bytes": 3 * 4096,
                    "model": "CEIL4096_REGULAR_PAYLOAD_OR_EXACT_CODE_WRITE_BOUND",
                    "assumptions": ["CONTROLLED_UNIT_FIXTURE_ONLY"], "evidence": {"uri": uri, "sha256": "b" * 64}}]},
            "admission_envelope_bytes": 1024**3 + 3 * 4096,
            "four_GiB_residual_reserve_excluded_from_envelope": True,
            "local_measurement_relabelled_as_target_runner": False,
            "reference_retention_GREEN_claimed": False, "temporal_peak_guarantee_claimed": False}


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.policy = preflight.load_policy()
        self.tmp = tempfile.TemporaryDirectory(prefix="rc6-capacity-cheap-")
        self.path = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def receipt(self):
        return preflight.build_receipt(policy=self.policy, path=self.path, expected_peak_bytes=1024**3,
            binding=binding(), comparable_peak=comparable_peak())

    def test_bytes_and_inode_boundaries_are_independent(self):
        args = dict(free_bytes=5 * 1024**3, total_inodes=1000, free_inodes=100,
                    expected_peak_bytes=1024**3, residual_reserve_bytes=4 * 1024**3,
                    minimum_free_inode_ratio=0.1)
        self.assertEqual(preflight.evaluate_capacity(**args)["status"], "GREEN")
        self.assertEqual(preflight.evaluate_capacity(**dict(args, free_bytes=args["free_bytes"] - 1))["blockers"],
                         ["CAPACITY_BYTES_INSUFFICIENT"])
        self.assertEqual(preflight.evaluate_capacity(**dict(args, free_inodes=99))["blockers"],
                         ["CAPACITY_INODES_INSUFFICIENT"])
        # An adequate byte floor does not prove the retained-entry quota.
        self.assertNotIn("retention_GREEN", preflight.evaluate_capacity(**args))

    def test_real_carrier_canonical_binding_with_slash_has_valid_receipt(self):
        from scripts import rc6_material_carrier as carrier
        args = SimpleNamespace(source_sha="a" * 40, source_tree="b" * 40, owner_session="controlled-owner")
        with mock.patch.dict(os.environ, {"GITHUB_RUN_ID": "471", "GITHUB_RUN_ATTEMPT": "2"}):
            bound = carrier.capacity_binding(args, "focal311")
        self.assertEqual(bound["runner_class"], "github-hosted/ubuntu-24.04")
        peak = comparable_peak()
        peak.update({name: bound[name] for name in ("producer", "runner_class", "workload_fingerprint")})
        receipt = preflight.build_receipt(policy=self.policy, path=self.path, expected_peak_bytes=1024**3,
            binding=bound, comparable_peak=peak)
        self.assertEqual(receipt["binding"], bound)
        for runner in ("https://unknown/runner", "github-hosted/ubuntu-24.04/extra", "ubuntu-24.04", True):
            with self.assertRaises(ValueError):
                preflight.validate_binding(dict(bound, runner_class=runner))

    def test_unknown_bool_nan_and_negative_measurements_fail_closed(self):
        args = dict(free_bytes=10000, total_inodes=1000, free_inodes=100,
                    expected_peak_bytes=1000, residual_reserve_bytes=1000, minimum_free_inode_ratio=0.1)
        for name, value in (("free_bytes", True), ("total_inodes", 0), ("free_inodes", -1),
                            ("expected_peak_bytes", 0), ("expected_peak_bytes", True),
                            ("residual_reserve_bytes", False), ("minimum_free_inode_ratio", float("nan")),
                            ("minimum_free_inode_ratio", True), ("free_inodes", 1001)):
            with self.assertRaises(ValueError):
                preflight.evaluate_capacity(**dict(args, **{name: value}))

    def test_peak_must_be_cited_hashed_comparable_and_not_lowered(self):
        for fault in ("missing", "digest", "workload", "runner", "producer", "lower", "uri"):
            peak = comparable_peak()
            if fault == "missing": peak = None
            elif fault == "digest": peak["evidence"]["sha256"] = "0" * 64
            elif fault in ("workload", "runner", "producer"):
                key = {"workload": "workload_fingerprint", "runner": "runner_class", "producer": "producer"}[fault]
                peak[key] = "different"
            elif fault == "lower": peak["peak_allocated_bytes"] -= 1
            elif fault == "uri": peak["evidence"]["uri"] = "file:///tmp/unverified"
            with self.assertRaises(ValueError):
                preflight.build_receipt(policy=self.policy, path=self.path, expected_peak_bytes=1024**3,
                    binding=binding(), comparable_peak=peak)

    def test_policy_cannot_weaken_hard_resource_or_capacity_contract(self):
        for group, key, value in (("local_capacity", "residual_reserve_bytes", 2 * 1024**3),
                ("local_capacity", "minimum_free_inode_ratio", 0.09),
                ("local_capacity", "maximum_receipt_age_seconds", 61),
                ("performance", "qualification_target_seconds", 90),
                ("performance", "maximum_rss_bytes", 3 * 1024**3),
                ("performance", "maximum_retained_entries", 200000)):
            policy = copy.deepcopy(self.policy)
            policy[group][key] = value
            with self.assertRaises(ValueError):
                preflight.validate_policy(policy)

    def test_live_receipt_binds_exact_candidate_attempt_and_filesystem(self):
        receipt = self.receipt()
        live = preflight.validate_live_receipt(path=self.path, receipt=receipt, binding=binding(), policy=self.policy)
        self.assertEqual(live["prior_receipt_sha256"], receipt["receipt_sha256"])
        self.assertEqual(live["filesystem"]["filesystem_device"], self.path.stat().st_dev)
        for field in ("candidate_sha", "candidate_tree", "attempt_id", "owner_id"):
            other = binding()
            other[field] = ("d" * 40 if field.startswith("candidate_") else "different")
            with self.assertRaisesRegex(ValueError, "BINDING_MISMATCH"):
                preflight.validate_live_receipt(path=self.path, receipt=receipt, binding=other, policy=self.policy)

    def test_stale_tampered_and_rebound_paths_cannot_authorize_start(self):
        receipt = self.receipt()
        with mock.patch.object(preflight.time, "time_ns", return_value=receipt["measured_at_unix_ns"] + 61 * 10**9):
            with self.assertRaisesRegex(ValueError, "STALE"):
                preflight.validate_live_receipt(path=self.path, receipt=receipt, binding=binding(), policy=self.policy)
        bad = copy.deepcopy(receipt)
        bad["capacity"]["free_bytes"] += 1
        with self.assertRaisesRegex(ValueError, "DIGEST_INVALID"):
            preflight.validate_live_receipt(path=self.path, receipt=bad, binding=binding(), policy=self.policy)
        old = self.path / "old-root"
        old.mkdir()
        receipt = preflight.build_receipt(policy=self.policy, path=old, expected_peak_bytes=1024**3,
            binding=binding(), comparable_peak=comparable_peak())
        old.rename(self.path / "preserved-old-root")
        old.mkdir()
        with self.assertRaisesRegex(ValueError, "FILESYSTEM_REBOUND"):
            preflight.validate_live_receipt(path=old, receipt=receipt, binding=binding(), policy=self.policy)

    def test_capacity_is_remeasured_at_consumption(self):
        receipt = self.receipt()
        measured = preflight.measure_filesystem(self.path)
        measured["free_bytes"] = 0
        with mock.patch.object(preflight, "measure_filesystem", return_value=measured):
            with self.assertRaisesRegex(ValueError, "LIVE_CAPACITY_INSUFFICIENT"):
                preflight.validate_live_receipt(path=self.path, receipt=receipt, binding=binding(), policy=self.policy)

    def test_symlink_or_symlink_parent_is_refused_without_following_target(self):
        target = self.path / "target"
        target.mkdir()
        alias = self.path / "alias"
        alias.symlink_to(target, target_is_directory=True)
        with self.assertRaises(OSError):
            preflight.measure_filesystem(alias)
        (target / "child").mkdir()
        with self.assertRaises(OSError):
            preflight.measure_filesystem(alias / "child")

    def test_envelope_preserves_original_allocation_and_separate_reserve(self):
        peak = comparable_envelope()
        original = copy.deepcopy(peak["reference"])
        amount = preflight.envelope_allocated_bytes(peak)
        receipt = preflight.build_receipt(policy=self.policy, path=self.path, expected_peak_bytes=amount,
            binding=binding(), comparable_peak=peak)
        self.assertEqual(peak["reference"], original)
        self.assertEqual(receipt["comparable_peak"]["reference"]["retained_entries"], 226749)
        self.assertIs(receipt["comparable_peak"]["reference_retention_GREEN_claimed"], False)
        self.assertEqual(receipt["capacity"]["expected_peak_bytes"], 1024**3 + 3 * 4096)
        self.assertEqual(receipt["capacity"]["required_free_bytes"], amount + 4 * 1024**3)
        self.assertEqual(receipt["filesystem"]["allocation_unit_bytes"], os.statvfs(self.path).f_frsize)
        self.assertTrue(receipt["filesystem"]["filesystem_type"])
        self.assertEqual(receipt["admission_basis"], "OBSERVED_ALLOCATION_PLUS_VERIFIED_BOUNDED_ADDITIONS")
        self.assertIs(receipt["promotion_or_runtime_validation_claimed"], False)
        self.assertEqual(preflight.envelope_allocated_bytes(comparable_peak()), 1024**3)

    def test_envelope_partial_unknown_rebound_and_weakened_claims_fail_closed(self):
        changes = [(("reference", "measurement_complete"), False),
            (("reference", "measurement_kind"), "TEMPORAL_PEAK"),
            (("reference", "allocated_bytes"), True), (("reference", "retained_entries"), False),
            (("reference", "evidence", "measurement_sha256"), "0" * 64),
            (("reference", "evidence", "measurement", "allocated_bytes"), 1),
            (("reference", "evidence", "raw_member"), "../../foreign"),
            (("reference", "evidence", "uri"), "file:///tmp/foreign"),
            (("target", "workload_fingerprint"), "f" * 64),
            (("comparison", "candidate_sha"), "d" * 40), (("comparison", "candidate_tree"), "e" * 40),
            (("comparison", "source_manifest_sha256"), "UNKNOWN"),
            (("comparison", "target_graph_evidence", "sha256"), "0" * 64),
            (("comparison", "unknown_components"), ["bootstrap_pip_fetch"]),
            (("comparison", "additional_bound_components", 0, "quantity"), 2),
            (("comparison", "additional_bound_components", 0, "unit_bound_bytes"), float("nan")),
            (("comparison", "additional_bound_components", 0, "allocated_bound_bytes"), True),
            (("comparison", "additional_bound_components", 0, "assumptions"), []),
            (("comparison", "additional_bound_components", 0, "evidence", "sha256"), "UNKNOWN"),
            (("admission_envelope_bytes",), 1024**3),
            (("four_GiB_residual_reserve_excluded_from_envelope",), False),
            (("temporal_peak_guarantee_claimed",), True),
            (("local_measurement_relabelled_as_target_runner",), True),
            (("reference_retention_GREEN_claimed",), True)]
        changes += [(("comparison", "checks", name), False) for name in sorted(preflight.COMPARISON_CHECKS)]
        for path, value in changes:
            peak = comparable_envelope()
            node = peak
            for part in path[:-1]:
                node = node[part]
            node[path[-1]] = value
            with self.assertRaises(ValueError):
                preflight.validate_comparable_peak(peak, binding(), 1024**3 + 3 * 4096)
        peak = comparable_envelope()
        peak["comparison"]["additional_bound_components"].append(
            copy.deepcopy(peak["comparison"]["additional_bound_components"][0]))
        with self.assertRaisesRegex(ValueError, "DUPLICATE"):
            preflight.validate_comparable_peak(peak, binding(), 1024**3 + 3 * 4096)

    def test_diagnostic_reference_cannot_qualify_canonical_runner(self):
        bound = dict(binding(), runner_class="github-hosted/ubuntu-24.04")
        peak = comparable_envelope(bound)
        with self.assertRaisesRegex(ValueError, "DIAGNOSTIC_REFERENCE_CANNOT_QUALIFY"):
            preflight.build_receipt(policy=self.policy, path=self.path,
                expected_peak_bytes=preflight.envelope_allocated_bytes(peak), binding=bound, comparable_peak=peak)

    def test_envelope_allocation_model_is_verified_from_live_mount(self):
        peak = comparable_envelope()
        measured = preflight.measure_filesystem(self.path)
        for value in (8192, True, None):
            changed = dict(measured, allocation_unit_bytes=value)
            with mock.patch.object(preflight, "measure_filesystem", return_value=changed):
                with self.assertRaisesRegex(ValueError, "ALLOCATION_MODEL_NOT_LIVE_VALIDATED"):
                    preflight.build_receipt(policy=self.policy, path=self.path,
                        expected_peak_bytes=preflight.envelope_allocated_bytes(peak), binding=binding(),
                        comparable_peak=peak)
        bound = dict(binding(), runner_class="github-hosted/ubuntu-24.04")
        peak = comparable_envelope(bound)
        # This synthetic origin update tests only the native type gate. It is
        # not a real LOCAL observation or Actions promotion receipt.
        peak["reference"]["runner_class"] = "LOCAL_MANAGED_WORKSPACE"
        peak["reference"]["evidence"]["measurement"]["origin_runner_class"] = "LOCAL_MANAGED_WORKSPACE"
        peak["reference"]["evidence"]["measurement_sha256"] = preflight.digest(peak["reference"]["evidence"]["measurement"])
        with mock.patch.object(preflight, "measure_filesystem", return_value=dict(measured, filesystem_type="tmpfs")):
            with self.assertRaisesRegex(ValueError, "FILESYSTEM_TYPE_NOT_CANONICAL"):
                preflight.build_receipt(policy=self.policy, path=self.path,
                    expected_peak_bytes=preflight.envelope_allocated_bytes(peak), binding=bound, comparable_peak=peak)

    def test_envelope_live_check_preserves_proof_and_rejects_model_rebinding(self):
        peak = comparable_envelope()
        receipt = preflight.build_receipt(policy=self.policy, path=self.path,
            expected_peak_bytes=preflight.envelope_allocated_bytes(peak), binding=binding(), comparable_peak=peak)
        live = preflight.validate_live_receipt(path=self.path, receipt=receipt, binding=binding(), policy=self.policy)
        self.assertEqual(live["comparable_peak"], peak)
        self.assertEqual(live["prior_receipt_sha256"], receipt["receipt_sha256"])
        measured = preflight.measure_filesystem(self.path)
        with mock.patch.object(preflight, "measure_filesystem", return_value=dict(measured, filesystem_type="unknown")):
            with self.assertRaisesRegex(ValueError, "FILESYSTEM_REBOUND"):
                preflight.validate_live_receipt(path=self.path, receipt=receipt, binding=binding(), policy=self.policy)


if __name__ == "__main__":
    unittest.main()
