"""A385 adversarial contracts; no real provider, host, DB or credentials."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "a385_probe", ROOT / "scripts/rc6_a385_ten_family_snapshot_readonly.py"
)
probe = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(probe)


def fixture(now):
    rows = []
    for index, family in enumerate(probe.FAMILIES):
        rows.append({
            "symbol": f"PRIVATE_TEST_SYMBOL_{index}",
            "family": family,
            "market": "BCBA", "currency": "ARS", "settlement": "T1",
            "canonical_identity": [f"PRIVATE_TEST_SYMBOL_{index}", family,
                                   "BYMA", "ARS", "A-24HS"],
            "paper_contract_ready": True,
            "paper_auto_enabled": False,
            "entry_authority": False,
            "real_money_authorized": False,
            "reason": "CONTRACT_READY_WITH_FRESH_SOURCE",
            "sources": {"PPI": {"selection_eligible": True}},
        })
    return {
        "schema": probe.SCHEMA,
        "generated_at": now.isoformat(),
        "decision_effect": "OBSERVE_ONLY",
        "real_money_authorized": False,
        "source_order": "PPI_PRIMARY_IOL_COMPLEMENTARY_BYMA_PUBLIC_SCRAPE",
        "universe": {"catalog_count": len(rows), "ledger_count": len(rows), "ledger_complete": True},
        "counts": {"paper_contract_ready": len(rows), "paper_auto_enabled": 0},
        "families": [
            {"family": name, "instrument_count": 1, "paper_contract_ready": 1,
             "paper_auto_enabled": 0}
            for name in probe.FAMILIES
        ],
        "instruments": rows,
    }


class TenFamilySnapshotContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "snapshot.json"
        self.now = datetime.now(timezone.utc)
        self.data = fixture(self.now)

    def write(self, data=None, *, age=0):
        self.path.write_text(json.dumps(self.data if data is None else data), encoding="utf-8")
        instant = (self.now - timedelta(seconds=age)).timestamp()
        os.utime(self.path, (instant, instant))
        return self.path

    def result(self, data=None, *, age=0):
        return probe.inspect_snapshot(self.write(data, age=age), current=self.now)

    def test_full_denominator_export_not_paper_admission(self):
        report = self.result()
        self.assertEqual(report["status"], "VALID_SOURCE_EXPORT_ONLY")
        self.assertEqual(report["families_present"], 10)
        self.assertEqual(report["universe_instruments"], 10)
        self.assertEqual(report["identity_semantic_mismatch_count"], 0)
        self.assertTrue(report["read_only"])
        self.assertFalse(report["admission_authority"])
        self.assertFalse(report["causal_pit_receipt_validated"])
        self.assertFalse(report["fees_risk_and_fill_lineage_validated"])
        self.assertFalse(report["runtime_sha_verified"])
        self.assertFalse(report["db_opened"])
        self.assertEqual(report["provider_calls"], 0)
        self.assertFalse(report["order_routes_called"])
        self.assertEqual(report["families"][0]["first_known_at_not_exported"], 1)
        self.assertNotIn("PRIVATE_TEST_SYMBOL_", json.dumps(report))

    def test_one_holding_sibling_does_not_disqualify_ready_instrument(self):
        case = deepcopy(self.data)
        blocked = deepcopy(case["instruments"][0])
        blocked["symbol"] = "PRIVATE_BLOCKED_SIBLING"
        blocked["canonical_identity"] = None
        blocked["paper_contract_ready"] = False
        blocked["reason"] = "NO_FRESH_STRUCTURED_SOURCE:UNKNOWN"
        blocked["sources"] = {"PPI": {"selection_eligible": False}}
        case["instruments"].append(blocked)
        case["families"][0]["instrument_count"] = 2
        case["universe"].update(catalog_count=11, ledger_count=11)
        result = self.result(case)
        self.assertEqual(result["status"], "VALID_SOURCE_EXPORT_ONLY")
        self.assertEqual(result["families"][0]["status"], "SOME_SOURCE_CONTRACT_READY")
        self.assertEqual(result["families"][0]["source_contract_ready"], 1)
        self.assertEqual(result["families"][0]["source_contract_not_ready"], 1)
        self.assertEqual(result["families"][0]["identity_unresolved"], 1)
        self.assertEqual(result["families"][0]["source_selection_absent"], 1)

    def test_stale_beyond_two_source_cycles_is_not_ready(self):
        case = deepcopy(self.data)
        case["generated_at"] = (self.now - timedelta(seconds=1200)).isoformat()
        result = self.result(case, age=1200)
        self.assertEqual(result["status"], "NO_VERIFICADO")
        self.assertEqual(result["reason"], "SNAPSHOT_STALE")
        self.assertEqual(result["families_present"], 10)  # Historical only.

    def test_future_timestamp_is_rejected(self):
        case = deepcopy(self.data)
        case["generated_at"] = (self.now + timedelta(minutes=3)).isoformat()
        self.assertEqual(self.result(case)["reason"], "SNAPSHOT_CLOCK_INCONSISTENT")

    def test_missing_family_is_visible_without_global_veto(self):
        case = deepcopy(self.data)
        case["instruments"].pop()
        case["families"].pop()
        case["universe"].update(catalog_count=9, ledger_count=9)
        case["counts"]["paper_contract_ready"] = 9
        result = self.result(case)
        self.assertEqual(result["status"], "NO_VERIFICADO")
        self.assertEqual(result["reason"], "FAMILY_COVERAGE_INCOMPLETE")
        self.assertEqual(result["families"][-1]["status"], "NO_FAMILY_SAMPLE_IN_EXPORT")
        self.assertEqual(result["families"][0]["status"], "ALL_SOURCE_CONTRACT_READY")

    def test_schema_and_complete_denominator_fail_closed(self):
        for mutation, reason in (("schema", "SNAPSHOT_SCHEMA_INVALID"),
                                 ("universe", "UNIVERSE_DENOMINATOR_UNVERIFIED"),
                                 ("counts", "GLOBAL_READY_COUNT_MISMATCH")):
            with self.subTest(mutation):
                case = deepcopy(self.data)
                if mutation == "schema":
                    case["schema"] = "unrelated"
                elif mutation == "universe":
                    case["universe"]["ledger_count"] = 9
                else:
                    case["counts"]["paper_contract_ready"] = 999
                self.assertEqual(self.result(case)["reason"], reason)

    def test_per_family_report_count_contradiction_is_not_green(self):
        case = deepcopy(self.data)
        case["families"][0]["paper_contract_ready"] = 0
        self.assertEqual(self.result(case)["reason"], "FAMILY_REPORTED_COUNT_MISMATCH")

    def test_any_autotrade_authority_is_rejected(self):
        for field, value, reason in (("paper_auto_enabled", True, "INSTRUMENT_PAPER_GUARD_UNVERIFIED"),
                                     ("entry_authority", True, "INSTRUMENT_PAPER_GUARD_UNVERIFIED"),
                                     ("real_money_authorized", True, "INSTRUMENT_PAPER_GUARD_UNVERIFIED")):
            with self.subTest(field):
                case = deepcopy(self.data)
                case["instruments"][0][field] = value
                self.assertEqual(self.result(case)["reason"], reason)

    def test_missing_file_and_symlink_are_blocked_without_following(self):
        self.assertEqual(probe.inspect_snapshot(self.path, current=self.now)["reason"], "SNAPSHOT_NOT_PUBLISHED")
        real = self.root / "real.json"
        real.write_text(json.dumps(self.data), encoding="utf-8")
        self.path.symlink_to(real)
        self.assertEqual(probe.inspect_snapshot(self.path, current=self.now)["reason"], "SNAPSHOT_OPEN_UNAVAILABLE")

    def test_symlink_parent_and_hardlink_are_blocked(self):
        target = self.root / "real"
        target.mkdir()
        original = target / "snapshot.json"
        original.write_text(json.dumps(self.data), encoding="utf-8")
        symlink_parent = self.root / "link"
        symlink_parent.symlink_to(target, target_is_directory=True)
        self.assertEqual(probe.inspect_snapshot(symlink_parent / "snapshot.json", current=self.now)["reason"], "SNAPSHOT_PATH_UNSAFE")
        os.link(original, self.path)
        self.assertEqual(probe.inspect_snapshot(self.path, current=self.now)["reason"], "SNAPSHOT_FILE_UNSAFE")

    def test_max_size_and_invalid_json(self):
        self.path.write_bytes(b"x" * 2048)
        self.assertEqual(probe.inspect_snapshot(self.path, current=self.now, max_bytes=1024)["reason"], "SNAPSHOT_BYTES_OUT_OF_BOUND")
        self.path.write_text("{not-json}", encoding="utf-8")
        self.assertEqual(probe.inspect_snapshot(self.path, current=self.now)["reason"], "SNAPSHOT_JSON_INVALID")

    def test_unmapped_family_is_counted_without_identity_leak(self):
        case = deepcopy(self.data)
        case["instruments"][0]["family"] = "UNKNOWN_UNMAPPED"
        case["families"][0]["family"] = "UNKNOWN_UNMAPPED"
        result = self.result(case)
        self.assertEqual(result["reason"], "IDENTITY_CENSUS_REVIEW_REQUIRED")
        self.assertEqual(result["unmapped_family_instruments"], 1)

    def test_duplicate_five_key_identity_is_flagged(self):
        case = deepcopy(self.data)
        case["instruments"][1]["canonical_identity"] = case["instruments"][0]["canonical_identity"]
        self.assertEqual(self.result(case)["reason"], "IDENTITY_CENSUS_REVIEW_REQUIRED")

    def test_ordered_five_key_permutation_and_identity_conflicts_fail_closed(self):
        for mutation in ("permuted", "wrong_market", "wrong_currency", "wrong_settlement"):
            with self.subTest(mutation):
                case = deepcopy(self.data)
                row = case["instruments"][0]
                if mutation == "permuted":
                    row["canonical_identity"] = [row["symbol"], "BCBA", "ARS", "T1", row["family"]]
                elif mutation == "wrong_market":
                    row["canonical_identity"][2] = "A3"
                elif mutation == "wrong_currency":
                    row["canonical_identity"][3] = "USD"
                else:
                    row["canonical_identity"][4] = "INMEDIATA"
                result = self.result(case)
                self.assertEqual(result["status"], "NO_VERIFICADO")
                self.assertEqual(result["reason"], "IDENTITY_CENSUS_REVIEW_REQUIRED")
                self.assertEqual(result["identity_semantic_mismatch_count"], 1)
                self.assertNotIn(row["symbol"], json.dumps(result))

    def test_market_and_settlement_aliases_preserve_valid_exact_identity(self):
        case = deepcopy(self.data)
        row = case["instruments"][0]
        row["market"], row["canonical_identity"][2] = "ROFEX", "A3"
        row["settlement"], row["canonical_identity"][4] = "CI", "INMEDIATA"
        result = self.result(case)
        self.assertEqual(result["status"], "VALID_SOURCE_EXPORT_ONLY")
        self.assertEqual(result["identity_semantic_mismatch_count"], 0)

    def test_malicious_freeform_reason_never_exports(self):
        case = deepcopy(self.data)
        case["instruments"][0]["reason"] = "secret=PRIVATE_SENSITIVE_PAYLOAD; symbol=AL30"
        result = self.result(case)
        self.assertEqual(result["status"], "VALID_SOURCE_EXPORT_ONLY")
        self.assertEqual(result["families"][0]["reason_counts"], {"UNCLASSIFIED_REASON": 1})
        self.assertNotIn("PRIVATE_SENSITIVE_PAYLOAD", json.dumps(result))

    def test_source_authority_cannot_be_replaced(self):
        case = deepcopy(self.data)
        case["source_order"] = "IOL_PRIMARY_PPI_COMPLEMENTARY"
        self.assertEqual(self.result(case)["reason"], "PPI_IDENTITY_AUTHORITY_UNVERIFIED")


if __name__ == "__main__":
    unittest.main()
