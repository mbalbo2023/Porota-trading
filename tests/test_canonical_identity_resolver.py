import unittest

from canonical_identity_resolver import resolve_canonical_identity


class CanonicalIdentityResolverTests(unittest.TestCase):
    def setUp(self):
        self.base = {
            "family": "ACCIONES",
            "subfamily": "ORDINARY",
            "ticker": "GGAL",
            "market": "ARGENTINA",
            "venue": "BYMA",
            "currency": "ARS",
            "settlement": "24 horas",
            "source": "PPI",
            "provider_id": "101",
        }

    def test_agreeing_sources_resolve_and_keep_provenance_ids(self):
        records = [self.base, {**self.base, "source": "IOL", "provider_id": "GGAL"}]
        result = resolve_canonical_identity(records)
        self.assertEqual(result.status, "RESOLVED")
        self.assertEqual(result.identity.settlement, "24_HORAS")
        self.assertEqual(dict(result.provider_ids)["PPI"], ("101",))
        self.assertTrue(result.canonical_id.startswith("ci:v1:"))

    def test_ticker_alone_never_resolves(self):
        result = resolve_canonical_identity([{"ticker": "GGAL", "source": "PPI", "provider_id": "101"}])
        self.assertEqual(result.status, "INSUFFICIENT")
        self.assertIn("family", result.missing)
        self.assertIsNone(result.canonical_id)

    def test_identity_without_provenance_id_is_insufficient(self):
        record = {key: value for key, value in self.base.items() if key not in {"source", "provider_id"}}
        result = resolve_canonical_identity([record])
        self.assertEqual(result.status, "INSUFFICIENT")
        self.assertIn("provider_id", result.missing)

    def test_conflicting_currency_blocks_resolution(self):
        records = [self.base, {**self.base, "currency": "USD", "source": "IOL", "provider_id": "GGAL"}]
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

    def test_cedear_etf_and_linked_letter_keep_subfamily(self):
        cedear_etf = resolve_canonical_identity([{**self.base, "family": "CEDEAR ETF", "subfamily": "ETF", "underlying": "IVV"}])
        linked = resolve_canonical_identity([{**self.base, "family": "LETRA LINKED", "subfamily": "LINKED"}])
        self.assertEqual(cedear_etf.identity.family, "CEDEARS")
        self.assertEqual(cedear_etf.identity.subfamily, "ETF")
        self.assertEqual(linked.identity.family, "LETRAS")
        self.assertEqual(linked.identity.subfamily, "LINKED")

    def test_cedear_requires_underlying(self):
        record = {**self.base, "family": "CEDEAR", "subfamily": "ORDINARY"}
        result = resolve_canonical_identity([record])
        self.assertEqual(result.status, "INSUFFICIENT")
        self.assertIn("underlying", result.missing)

    def test_options_require_series_dimensions(self):
        record = {**self.base, "family": "OPCIONES", "subfamily": "CALL"}
        result = resolve_canonical_identity([record])
        self.assertEqual(result.status, "INSUFFICIENT")
        self.assertTrue({"underlying", "expiry", "strike", "put_call"}.issubset(result.missing))

    def test_two_ids_from_same_provider_are_a_conflict(self):
        records = [self.base, {**self.base, "provider_id": "102"}]
        result = resolve_canonical_identity(records)
        self.assertEqual(result.status, "CONFLICT")
        self.assertIn("provider_id", result.conflicts)

    def test_optional_enrichment_does_not_change_canonical_id(self):
        first = resolve_canonical_identity([self.base])
        enriched = resolve_canonical_identity([{**self.base, "issuer": "Example Issuer", "share_class": "A"}])
        self.assertEqual(first.canonical_id, enriched.canonical_id)

    def test_optional_identity_discriminator_conflict_blocks_resolution(self):
        records = [
            {**self.base, "underlying": "GGAL"},
            {**self.base, "underlying": "YPFD", "source": "IOL", "provider_id": "YPFD"},
        ]
        result = resolve_canonical_identity(records)
        self.assertEqual(result.status, "CONFLICT")
        self.assertIn("underlying", result.conflicts)

    def test_caucion_placer_and_taker_are_distinct_subfamilies(self):
        placer = resolve_canonical_identity([{
            **self.base, "family": "CAUCION COLOCADORA", "subfamily": "COLOCADORA",
        }])
        taker = resolve_canonical_identity([{
            **self.base, "family": "CAUCION TOMADORA", "subfamily": "TOMADORA",
        }])
        self.assertEqual(placer.identity.subfamily, "COLOCADORA")
        self.assertEqual(taker.identity.subfamily, "TOMADORA")
        self.assertNotEqual(placer.canonical_id, taker.canonical_id)

    def test_etf_subfamily_prevents_direct_inverse_collapse(self):
        direct = resolve_canonical_identity([{**self.base, "family": "ETF", "subfamily": "STANDARD"}])
        inverse = resolve_canonical_identity([{**self.base, "family": "ETF", "subfamily": "INVERSE"}])
        self.assertNotEqual(direct.canonical_id, inverse.canonical_id)

    def test_unmapped_settlement_aliases_conflict_instead_of_being_guessed(self):
        records = [
            self.base,
            {**self.base, "settlement": "T+1", "source": "IOL", "provider_id": "GGAL"},
        ]
        result = resolve_canonical_identity(records)
        self.assertEqual(result.status, "CONFLICT")
        self.assertIn("settlement", result.conflicts)

    def test_d_and_c_are_not_guessed_as_currencies(self):
        for code in ("D", "C"):
            result = resolve_canonical_identity([{**self.base, "currency": code}])
            self.assertEqual(result.status, "INSUFFICIENT")
            self.assertIn("currency", result.missing)

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
