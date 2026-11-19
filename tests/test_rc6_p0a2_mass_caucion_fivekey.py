"""P0-A2: a discovery request cannot mint PAPER five-key contract evidence.

All fixtures are synthetic and in memory: no broker GET, database or orders.
"""

import json
import unittest
from datetime import datetime, timedelta, timezone

import bu_instrument_catalog as catalog
import cp_contract_evidence_v2_hf6 as evidence_v2
import cr_contract_evidence_v2_mass_hf6 as mass
import rc6_contract_bridge as bridge


NOW = datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc)


def _primary(family="ACCIONES", *, native=False, raw_settlement=None,
             discovery="PPI_PRIMARY"):
    ticker = "TEST" if family == "ACCIONES" else "PESOS1"
    term = "A-24HS" if family == "ACCIONES" else "INMEDIATA"
    raw = {"ticker": ticker, "type": family, "market": "BYMA", "currency": "ARS",
           "_discovery_source": discovery}
    if native:
        raw["settlement"] = raw_settlement or term
    row = catalog.normalize_record(raw, term, NOW.isoformat(), "P0A2_OFFLINE")
    return {**row, "metadata_json": json.dumps(row["raw"])}


def _caucion_claims():
    from au_fee_schedule import CAUCION_PAPER_FEE_AUTHORITY

    def claim(source_class, payload):
        return {"family": "CAUCIONES", "ticker": "PESOS1", "market": "BYMA",
                "currency": "ARS", "settlement": "INMEDIATA",
                "source_class": source_class, "source_ref": "P0A2_OFFLINE_FIXTURE",
                "observed_at": NOW.isoformat(), "evidence": payload,
                "evidence_hash": evidence_v2.evidence_hash(payload)}

    return [
        claim("DERIVED_OFFICIAL_RULE", {
            "side": "COLOCADORA", "term_days": "1",
            "minimum_principal": "100000", "paper_principal_step": "1",
            "day_count_basis": 365, "fee_payment": "UPFRONT",
            "paper_fill_policy": "LIVE_PPI_BID_PARTICIPATION_CAP",
            "fee_authority": CAUCION_PAPER_FEE_AUTHORITY,
        }),
        claim("PPI_STRUCTURED_API", {
            "annual_rate_fraction": "0.25", "start_date": NOW.date().isoformat(),
            "maturity_at": (NOW + timedelta(days=1)).isoformat(),
            "quoted_at": NOW.isoformat(), "available_principal": "500000",
            "principal_step": "1", "market_session_state": "OPEN", "operable": True,
        }),
    ]


class TestP0A2MassFiveKey(unittest.TestCase):
    def test_request_only_settlement_is_discovery_not_provider_full_key(self):
        records, skipped, count = mass.planned_records([_primary()])
        self.assertEqual(records, [])
        self.assertEqual(count, {})
        self.assertEqual(skipped, {"PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED": 1})

    def test_native_literal_five_key_keeps_existing_mass_record(self):
        records, skipped, count = mass.planned_records([_primary(native=True)])
        self.assertEqual(skipped, {})
        self.assertEqual(count, {"ACCIONES": 1})
        self.assertEqual([item["source_class"] for item in records],
                         ["PPI_STRUCTURED_API", "DERIVED_OFFICIAL_RULE"])

    def test_forged_ppi_field_with_conflicting_native_raw_is_denied(self):
        row = _primary(native=True, raw_settlement="INMEDIATA")
        row["settlement"] = "A-24HS"
        records, skipped, count = mass.planned_records([row])
        self.assertEqual(records, [])
        self.assertEqual(count, {})
        self.assertEqual(skipped, {"PPI_FULL_KEY_NOT_PROVIDER_ATTESTED": 1})

    def test_complementary_discovery_with_ppi_looking_field_is_denied(self):
        row = _primary(native=True, discovery="IOL_COMPLEMENTARY")
        records, skipped, count = mass.planned_records([row])
        self.assertEqual(records, [])
        self.assertEqual(count, {})
        self.assertEqual(skipped, {"NOT_PPI_PRIMARY": 1})


class TestP0A2CaucionFiveKey(unittest.TestCase):
    def test_request_only_caucion_cannot_build_direct_paper_offer(self):
        with self.assertRaisesRegex(ValueError, "PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED"):
            bridge.caucion_offer_from_evidence(_caucion_claims(), _primary("CAUCIONES"), now=NOW)

    def test_native_literal_caucion_keeps_existing_paper_offer(self):
        offer = bridge.caucion_offer_from_evidence(
            _caucion_claims(), _primary("CAUCIONES", native=True), now=NOW)
        self.assertEqual((offer.instrument_id, offer.currency), ("PESOS1", "ARS"))

    def test_forged_ppi_field_with_mismatched_caucion_raw_is_denied(self):
        row = _primary("CAUCIONES", native=True, raw_settlement="A-24HS")
        row["settlement"] = "INMEDIATA"
        with self.assertRaisesRegex(ValueError, "PPI_FULL_KEY_NOT_PROVIDER_ATTESTED"):
            bridge.caucion_offer_from_evidence(_caucion_claims(), row, now=NOW)


if __name__ == "__main__":
    unittest.main()
