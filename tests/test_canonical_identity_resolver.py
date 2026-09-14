import unittest

from canonical_identity_resolver import resolve_canonical_identity


class CanonicalIdentityResolverTests(unittest.TestCase):
    def setUp(self):
        self.base = {
            "family": "ACCIONES",
            "ticker": "GGAL",
            "market": "ARGENTINA",
            "venue": "BYMA",
            "currency": "ARS",
            "settlement": "24 horas",
        }

    def test_agreeing_sources_resolve_and_keep_provenance_ids(self):
        records = [
            {**self.base, "source": "PPI", "provider_id": "101"},
            {**self.base, "source": "IOL", "provider_id": "GGAL"},
        ]
        result = resolve_canonical_identity(records)
        self.assertEqual(result.status, "RESOLVED")
        self.assertEqual(result.identity.settlement, "24_HORAS")
        self.assertEqual(dict(result.provider_ids)["PPI"], ("101",))
        self.assertTrue(result.canonical_id.startswith("ci:v1:"))

    def test_ticker_alone_never_resolves(self):
        result = resolve_canonical_identity([{"ticker": "GGAL"}])
        self.assertEqual(result.status, "INSUFFICIENT")
        self.assertIn("family", result.missing)
        self.assertIsNone(result.canonical_id)

    def test_conflicting_currency_blocks_resolution(self):
        records = [
            {**self.base, "source": "PPI", "provider_id": "101"},
            {**self.base, "currency": "USD", "source": "IOL", "provider_id": "GGAL"},
        ]
        result = resolve_canonical_identity(records)
        self.assertEqual(result.status, "CONFLICT")
        self.assertIn("currency", result.conflicts)
        self.assertIsNone(result.identity)

    def test_market_and_venue_are_separate_identity_fields(self):
        local = resolve_canonical_identity([self.base])
        foreign = resolve_canonical_identity([{
            **self.base, "market": "USA", "venue": "NASDAQ", "currency": "USD",
        }])
        self.assertEqual(local.status, "RESOLVED")
        self.assertEqual(foreign.status, "RESOLVED")
        self.assertNotEqual(local.canonical_id, foreign.canonical_id)

    def test_family_aliases_preserve_us_and_foreign_fund_distinctions(self):
        us = resolve_canonical_identity([{**self.base, "family": "ACCIONES USA"}])
        foreign_fund = resolve_canonical_identity([{**self.base, "family": "FCI exterior"}])
        self.assertEqual(us.identity.family, "ACCIONES_USA")
        self.assertEqual(foreign_fund.identity.family, "FCI_EXTERIOR")

    def test_etf_aliases_converge_without_collapsing_other_families(self):
        etf = resolve_canonical_identity([{**self.base, "family": "ETFS"}])
        equity = resolve_canonical_identity([self.base])
        self.assertEqual(etf.identity.family, "ETF")
        self.assertNotEqual(etf.canonical_id, equity.canonical_id)

    def test_two_ids_from_same_provider_are_a_conflict(self):
        records = [
            {**self.base, "source": "PPI", "provider_id": "101"},
            {**self.base, "source": "PPI", "provider_id": "102"},
        ]
        result = resolve_canonical_identity(records)
        self.assertEqual(result.status, "CONFLICT")
        self.assertIn("provider_id", result.conflicts)

    def test_optional_identity_discriminator_conflict_blocks_resolution(self):
        records = [
            {**self.base, "underlying": "GGAL", "source": "PPI"},
            {**self.base, "underlying": "YPFD", "source": "IOL"},
        ]
        result = resolve_canonical_identity(records)
        self.assertEqual(result.status, "CONFLICT")
        self.assertIn("underlying", result.conflicts)

    def test_malformed_evidence_fails_closed(self):
        result = resolve_canonical_identity([self.base, None])
        self.assertEqual(result.status, "CONFLICT")
        self.assertIn("malformed_record", result.conflicts)

    def test_currency_labels_normalize_known_ars_buckets(self):
        first = resolve_canonical_identity([{**self.base, "currency": "Pesos"}])
        second = resolve_canonical_identity([self.base])
        self.assertEqual(first.canonical_id, second.canonical_id)


if __name__ == "__main__":
    unittest.main()
