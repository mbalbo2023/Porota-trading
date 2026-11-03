"""A608 offline-only invariants; no provider/DB/host or broker fixtures."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rc6_a608_aggregate_counterfactual as audit  # noqa: E402


def a385_fixture():
    families = []
    gaps = {
        "ACCIONES": "CONTRACT_EVIDENCE_REVIEW_REQUIRED",
        "CEDEARS": "CONTRACT_EVIDENCE_REVIEW_REQUIRED",
        "BONOS": "NEEDS_NOMINAL_UNITS",
        "LETRAS": "NEEDS_NOMINAL_UNITS",
        "OBLIGACIONES": "NEEDS_NOMINAL_UNITS",
        "OPCIONES": "NEEDS_OPTION_CONTRACT",
        "FUTUROS": "NEEDS_FUTURES_MARGIN_AND_CONTRACT",
        "CAUCIONES": "NEEDS_CAUCION_TERMS",
        "FCI": "NEEDS_FUND_SETTLEMENT",
    }
    for name in (*audit.ACTIVE_FAMILIES, "ETFS"):
        n = 0 if name == "ETFS" else 2
        reasons = ({"NO_FRESH_STRUCTURED_SOURCE:READY_PAPER_SPOT": 1,
                    "NO_FRESH_STRUCTURED_SOURCE:" + gaps[name]: 1} if n else {})
        families.append({
            "family": name, "instruments": n,
            "source_contract_ready": 0, "source_contract_not_ready": n,
            "source_selection_absent": n, "first_known_at_not_exported": n,
            "identity_unresolved": 0, "reason_counts": reasons,
            "eligible_source_counts": {p: 0 for p in (
                "PPI", "IOL", "BYMA", "A3", "A3_CEM", "CAFCI", "CNV", "ROFEX")},
            "status": "NO_SOURCE_CONTRACT_READY" if n else "NO_FAMILY_SAMPLE_IN_EXPORT",
        })
    sources = []
    for provider in ("PPI", "IOL", "BYMA"):
        source = {"source": provider, "records": 18}
        source.update({k: 0 for k in (
            "present", "structured", "native_fresh", "native_stale",
            "native_unknown", "receipt_present", "exact_identity", "selection_eligible")})
        source["native_unknown"] = 18
        sources.append(source)
    return {
        "schema": audit.INPUT_SCHEMA, "source": "EXISTING_FULL_UNIVERSE_JSON_ONLY",
        "read_only": True, "db_opened": False, "provider_calls": 0,
        "order_routes_called": False, "admission_authority": False,
        "runtime_sha_verified": False, "causal_pit_receipt_validated": False,
        "status": "NO_VERIFICADO", "reason": "FAMILY_COVERAGE_INCOMPLETE",
        "families_expected": 10, "families_present": 9,
        "identity_semantic_mismatch_count": 0, "duplicate_exact_identity_count": 0,
        "unmapped_family_instruments": 0, "source_contract_ready_in_export": 0,
        "universe_instruments": 18, "mapped_family_instruments": 18,
        "source_sha256": "a" * 64, "source_bytes": 1000,
        "source_generated_at_utc": "2026-11-03T19:12:00+00:00",
        "source_diagnostics": sources, "families": families,
    }


class A608AggregateCounterfactualTests(unittest.TestCase):
    def setUp(self):
        self.payload = a385_fixture()

    def test_counterfactual_has_only_upper_bounds_not_paper_go(self):
        result = audit.counterfactual(self.payload, "b" * 64)
        self.assertEqual(result["status"], "AGGREGATE_CEILINGS_ONLY_NO_PAPER_GO")
        self.assertEqual(result["catalog_instruments"], 18)
        self.assertEqual(result["preliminary_ready_paper_total"], 9)
        self.assertEqual(result["explicit_contract_gap_total"], 9)
        self.assertEqual(result["known_at_missing_in_export"], 18)
        self.assertEqual(result["verified_paper_admissions_total"], 0)
        self.assertEqual(result["verified_postdecision_causal_fills_total"], 0)
        self.assertTrue(result["db_opened"] is False)
        self.assertFalse(result["order_routes_called"])
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(len(result["family_rows"]), 9)
        for family in result["family_rows"]:
            self.assertEqual(family["baseline_observed_source_contract_ready"], 0)
            self.assertEqual(family["contract_only_observed_source_gated_ceiling"], 0)
            self.assertEqual(family["ppi_pit_only_preliminary_contract_ceiling"], 1)
            self.assertEqual(family["both_ppi_and_contracts_theoretical_catalog_ceiling"], 2)
            self.assertEqual(family["verified_postdecision_causal_fills"], 0)
            self.assertEqual(family["per_instrument_gate_results"],
                             "NOT_CALCULABLE_AGGREGATE_ONLY")

    def test_fci_nav_is_not_treated_as_book_fill(self):
        result = audit.counterfactual(self.payload, "b" * 64)
        self.assertEqual(sum(row["fci_requires_causal_nav_not_book_fill"]
                             for row in result["family_rows"]), 1)
        self.assertTrue(result["family_rows"][-1]["fci_requires_causal_nav_not_book_fill"])

    def test_exact_raw_input_digest_is_mandatory(self):
        raw = json.dumps(self.payload, separators=(",", ":")).encode()
        digest = hashlib.sha256(raw).hexdigest()
        self.assertEqual(audit.load_pinned(raw, expected_sha256=digest)[1], digest)
        with self.assertRaisesRegex(audit.CounterfactualRejected,
                                    "A385_ARTIFACT_SHA256_MISMATCH"):
            audit.load_pinned(raw + b" ", expected_sha256=digest)

    def test_duplicate_json_keys_rejected_even_with_matching_digest(self):
        raw = b'{"schema":"first","schema":"second"}'
        with self.assertRaisesRegex(audit.CounterfactualRejected, "DUPLICATE_JSON_KEY"):
            audit.load_pinned(raw, expected_sha256=hashlib.sha256(raw).hexdigest())

    def test_unsafe_or_large_evidence_rejected(self):
        with self.assertRaisesRegex(audit.CounterfactualRejected, "A385_INPUT_SIZE_OR_TYPE_INVALID"):
            audit.load_pinned(b" " * (audit.MAX_INPUT_BYTES + 1), expected_sha256="a" * 64)
        with self.assertRaisesRegex(audit.CounterfactualRejected, "A385_EXPECTED_DIGEST_INVALID"):
            audit.load_pinned(b"{}", expected_sha256="NOT_A_SHA")

    def test_any_paper_ready_claim_rejected(self):
        value = deepcopy(self.payload)
        value["source_contract_ready_in_export"] = 1
        with self.assertRaisesRegex(audit.CounterfactualRejected,
                                    "A385_IDENTITY_OR_READY_COUNTER_CONFLICT"):
            audit.counterfactual(value, "b" * 64)

    def test_nonzero_ppi_evidence_rejected_for_this_pinned_zero_baseline(self):
        value = deepcopy(self.payload)
        value["source_diagnostics"][0]["present"] = 1
        with self.assertRaisesRegex(audit.CounterfactualRejected,
                                    "A385_SOURCE_SCENARIO_NOT_ZERO_BASELINE"):
            audit.counterfactual(value, "b" * 64)

    def test_known_at_missing_assumption_never_extrapolated(self):
        value = deepcopy(self.payload)
        value["families"][0]["first_known_at_not_exported"] = 1
        with self.assertRaisesRegex(audit.CounterfactualRejected,
                                    "A385_FAMILY_INTERSECTION_UNVERIFIED"):
            audit.counterfactual(value, "b" * 64)

    def test_no_leaky_extra_missing_family_sample(self):
        value = deepcopy(self.payload)
        value["families"][0]["family"] = "ETFS"
        with self.assertRaisesRegex(audit.CounterfactualRejected,
                                    "A385_FAMILY_IDENTITY_INVALID"):
            audit.counterfactual(value, "b" * 64)

    def test_secondary_provider_can_never_be_primary_readiness(self):
        value = deepcopy(self.payload)
        value["source_diagnostics"][1]["present"] = 5
        value["source_diagnostics"][1]["structured"] = 5
        value["source_diagnostics"][1]["native_stale"] = 5
        value["source_diagnostics"][1]["native_unknown"] = 13
        value["source_diagnostics"][1]["receipt_present"] = 5
        report = audit.counterfactual(value, "b" * 64)
        self.assertEqual(report["verified_paper_admissions_total"], 0)

    def test_missing_reason_rows_do_not_turn_into_candidates(self):
        value = deepcopy(self.payload)
        value["families"][1]["reason_counts"].pop(
            "NO_FRESH_STRUCTURED_SOURCE:READY_PAPER_SPOT")
        with self.assertRaisesRegex(audit.CounterfactualRejected, "A385_HISTOGRAM_INCOMPLETE"):
            audit.counterfactual(value, "b" * 64)

    def test_non_source_reason_prefix_cannot_be_extrapolated(self):
        value = deepcopy(self.payload)
        value["families"][0]["reason_counts"] = {"READY_PAPER_SPOT": 2}
        with self.assertRaisesRegex(audit.CounterfactualRejected, "A385_REASON_PREFIX_DIVERGED"):
            audit.counterfactual(value, "b" * 64)


if __name__ == "__main__":
    unittest.main()
