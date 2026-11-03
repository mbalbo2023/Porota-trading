"""A615 synthetic FI causal guards: no provider GET, economic sale or host DB."""
from copy import deepcopy
import unittest

from rc6_fixed_income_sale_causal_guard import (
    NATIVE_MARKET_SELL_AUTHORITY, PAPER_ONLY, REAL_ROUTES_USED, SCHEMA,
    assess_causal_paper_sale, assess_complementary_fx_arithmetic,
)


IDENTITY = ["AL30", "BONOS", "BYMA", "ARS", "A-24HS"]


def sample():
    book1 = {
        "identity": IDENTITY[:], "provider": "PPI_PRIMARY_READ_ONLY",
        "route": "MarketData/Book", "native_book_at": "2026-11-03T15:00:00Z",
        "observed_at": "2026-11-03T15:00:00.100000Z",
        "known_at": "2026-11-03T15:00:00.200000Z",
        "journal_event_sha256": "a" * 64,
        "bid": "102", "ask": "103", "bid_size": "100",
        "quantity_unit": "VN", "quote_price_basis": "PER_100_VN",
    }
    book2 = {
        **book1, "identity": IDENTITY[:],
        "native_book_at": "2026-11-03T15:00:02.200000Z",
        "observed_at": "2026-11-03T15:00:02.400000Z",
        "known_at": "2026-11-03T15:00:02.500000Z",
        "journal_event_sha256": "b" * 64,
        "bid": "104", "ask": "105", "bid_size": "100",
    }
    return {
        "identity": IDENTITY[:], "book1": book1,
        "decision": {
            "identity": IDENTITY[:], "side": "SELL",
            "decided_at": "2026-11-03T15:00:01Z",
            "known_at": "2026-11-03T15:00:01.100000Z",
            "request_id": "PAPER-FI-SALE-AL30-001",
            "decision_receipt_sha256": "c" * 64,
            "decision_signature_sha256": "d" * 64,
        },
        "book2": book2,
        "contract": {
            "identity": IDENTITY[:], "quantity_step": "1",
            "minimum_quantity": "1", "cash_multiplier": "0.01",
            "quote_price_basis": "PER_100_VN",
            # USD issuer payouts do not make ARS market sale cash a USD/ARS FX.
            "payout_currency": "USD",
        },
        "fee": {
            "identity": IDENTITY[:], "nominal": "70",
            "fee_currency": "ARS", "fee_amount": "0.61",
            "tariff_sha256": "e" * 64,
        },
        "settlement": {
            "identity": IDENTITY[:], "settlement_currency": "ARS",
            "settlement_at": "2026-11-04T15:00:00Z",
            "net_proceeds": "72.19",
            "settlement_policy_sha256": "f" * 64,
            "fx_receipt_sha256": None,
        },
        "requested_nominal": "70", "latency_seconds": "1",
        "execution_at": "2026-11-03T15:00:03Z",
    }


class FiNativeExitCausalityGuardTest(unittest.TestCase):
    def assess(self, **changes):
        data = sample()
        data.update(changes)
        return assess_causal_paper_sale(**data)

    def test_causal_structure_is_never_source_decision_or_sell_authority(self):
        data = sample()
        result = assess_causal_paper_sale(**data)
        self.assertEqual(result["schema"], SCHEMA)
        self.assertTrue(result["structural_witness"])
        self.assertEqual(result["structural_blockers"], [])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["native_market_sell_authorized"])
        self.assertEqual(result["economic_side_effect"], "0")
        self.assertEqual(result["real_orders_sent"], 0)
        self.assertTrue(PAPER_ONLY)
        self.assertFalse(NATIVE_MARKET_SELL_AUTHORITY or REAL_ROUTES_USED)
        self.assertIn("FI_EXIT_SIGNED_PAPER_DECISION_AUTHORITY_UNBOUND",result["blockers"])
        self.assertIn("FI_EXIT_PPI_BOOK_JOURNAL_AUTHORITY_UNBOUND",result["blockers"])
        # No IO, no call-site election, deterministic claim receipt / retry.
        self.assertEqual(assess_causal_paper_sale(**data), result)
        self.assertEqual(data, sample())

    def test_book2_is_strictly_post_decision_plus_latency(self):
        data = sample()
        data["book2"]["native_book_at"] = "2026-11-03T15:00:02Z"
        result = assess_causal_paper_sale(**data)
        self.assertIn("FI_EXIT_BOOK2_STRICT_FUTURE_CLOCK_INVALID",result["structural_blockers"])
        self.assertFalse(result["structural_witness"])
        data["book2"]["native_book_at"] = "2026-11-03T15:00:02.000001Z"
        result = assess_causal_paper_sale(**data)
        self.assertNotIn("FI_EXIT_BOOK2_STRICT_FUTURE_CLOCK_INVALID",result["structural_blockers"])

    def test_book1_lookahead_or_book2_known_after_execution_blocks(self):
        data = sample()
        data["book1"]["known_at"] = "2026-11-03T15:00:01.000001Z"
        self.assertIn("FI_EXIT_BOOK1_DECISION_CAUSAL_CLOCK_INVALID",
                      assess_causal_paper_sale(**data)["structural_blockers"])
        data = sample()
        data["book2"]["known_at"] = "2026-11-03T15:00:03.000001Z"
        self.assertIn("FI_EXIT_BOOK2_STRICT_FUTURE_CLOCK_INVALID",
                      assess_causal_paper_sale(**data)["structural_blockers"])

    def test_five_key_rejects_cross_currency_market_settlement_and_series(self):
        for dimension, replacement in ((2,"NYSE"),(3,"USD"),(4,"A-CI"),(0,"AL30D")):
            with self.subTest(dimension=dimension):
                data = sample()
                data["book2"]["identity"][dimension] = replacement
                self.assertIn("FI_EXIT_FIVE_KEY_IDENTITY_CONFLICT",
                              assess_causal_paper_sale(**data)["structural_blockers"])

    def test_duplicate_book_and_implicit_provider_claim_block(self):
        data = sample()
        data["book2"]["journal_event_sha256"] = data["book1"]["journal_event_sha256"]
        data["book2"]["provider"] = "IOL"
        result = assess_causal_paper_sale(**data)
        self.assertIn("FI_EXIT_BOOK2_EVENT_REPLAY", result["structural_blockers"])
        self.assertIn("FI_EXIT_SIDE_OR_PROVIDER_CLAIM_INVALID",result["structural_blockers"])

    def test_false_vn_unit_depth_lot_price_basis_and_fee_are_rejected(self):
        mutations = (
            ("book2", "quantity_unit", "LOTS", "FI_EXIT_PPI_BOOK_QUANTITY_UNIT_UNVERIFIED"),
            ("book2", "bid_size", "69", "FI_EXIT_VN_STEP_OR_NATIVE_DEPTH_INSUFFICIENT"),
            ("contract", "quantity_step", "100", "FI_EXIT_VN_STEP_OR_NATIVE_DEPTH_INSUFFICIENT"),
            ("contract", "cash_multiplier", "1", "FI_EXIT_PRICE_PER_VN_MULTIPLIER_CONFLICT"),
            ("book2", "quote_price_basis", "PER_1_VN", "FI_EXIT_PRICE_PER_VN_MULTIPLIER_CONFLICT"),
            ("fee", "nominal", "100", "FI_EXIT_TARIFF_NOMINAL_OR_CURRENCY_CONFLICT"),
            ("fee", "fee_currency", "USD", "FI_EXIT_TARIFF_NOMINAL_OR_CURRENCY_CONFLICT"),
            ("settlement", "net_proceeds", "7280", "FI_EXIT_SETTLEMENT_CURRENCY_TIME_OR_CASH_CONFLICT"),
        )
        for scope, key, value, reason in mutations:
            with self.subTest(scope=scope,key=key):
                data = sample()
                data[scope][key] = value
                self.assertIn(reason, assess_causal_paper_sale(**data)["structural_blockers"])

    def test_issuer_usd_does_not_infer_fx_for_ars_sale(self):
        result = self.assess()
        self.assertTrue(result["structural_witness"])
        self.assertNotIn("FI_EXIT_UNREQUESTED_FX_AUTHORITY_UNBOUND",result["blockers"])
        data = sample()
        data["settlement"]["fx_receipt_sha256"] = "1" * 64
        self.assertIn("FI_EXIT_UNREQUESTED_FX_AUTHORITY_UNBOUND",
                      assess_causal_paper_sale(**data)["structural_blockers"])

    def test_no_caller_assertion_can_elevate_authority(self):
        data = sample()
        data["decision"]["signature_verified"] = True
        with self.assertRaisesRegex(ValueError, "FI_EXIT_DECISION_SCHEMA_INVALID"):
            assess_causal_paper_sale(**data)
        data = sample()
        data["book2"]["journal_event_sha256"] = "synthetic"
        with self.assertRaisesRegex(ValueError, "FI_EXIT_BOOK2_EVENT_DIGEST_INVALID"):
            assess_causal_paper_sale(**data)

    def test_latency_submicrosecond_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "FI_EXIT_LATENCY_SUBMICROSECOND_UNSUPPORTED"):
            self.assess(latency_seconds="0.0000001")

    def test_claim_change_changes_digest_no_ledger_idempotence_is_claimed(self):
        first = self.assess()
        data = sample()
        data["book2"]["bid_size"] = "105"
        second = assess_causal_paper_sale(**data)
        self.assertNotEqual(first["claim_sha256"],second["claim_sha256"])
        self.assertFalse(first["native_market_sell_authorized"])
        self.assertFalse(second["native_market_sell_authorized"])

    def test_iol_al30_amount_rate_disagreement_never_promotes_ppi(self):
        # A614 follow-up: secondary IOL AL30 nominal 1, 0.5303909333 USD,
        # 835.7 ARS and rate=1; endpoint's actual rate semantics need evidence.
        result = assess_complementary_fx_arithmetic(
            foreign_amount="0.5303909333", local_amount="835.7",
            applied_rate="1", rate_convention="ARS_PER_USD")
        self.assertEqual(result["status"],"BLOCKED")
        self.assertFalse(result["arithmetic_consistent_if_named_convention"])
        self.assertIn("FI_EXIT_COMPLEMENTARY_FX_AMOUNT_RATE_MISMATCH",result["blockers"])
        self.assertFalse(result["ppi_primary_provenance_established"])
        self.assertFalse(result["fx_authorized"])
        with self.assertRaisesRegex(ValueError,"FI_EXIT_FX_RATE_CONVENTION_UNVERIFIED"):
            assess_complementary_fx_arithmetic(foreign_amount="1", local_amount="2",
                                                applied_rate="2",rate_convention="UNKNOWN")


if __name__ == "__main__":
    unittest.main()
