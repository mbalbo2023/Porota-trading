"""A224: source/strategy family-boundary regression guards (synthetic only).

These tests intentionally distinguish five different namespaces: PPI catalog,
SHADOW discovery, Contract Evidence v2, generic PAPER contract and strategy
observation.  A shared spelling is not proof of an executable instrument.
None of these tests invokes a provider, host, database, broker or order route.
Known cross-namespace gaps are recorded as fail-closed behavior, not as an
instruction to promote aliases or switch source authority.
"""

from datetime import datetime, timezone
import unittest

from bs_instrument_contracts import family_name
from cp_history_ingest_policy_hf6 import history_collection_capability
from cq_contract_readiness_hf6 import canonical_family
from cq_family_contract_rules_hf6 import FAMILY_CONTRACT_FIELDS
from cy_market_source_arbitration_hf6 import (
    SourceSnapshot, primary_live_source, select_live_snapshot,
)
from rc6_dynamic_universe import sources
from rc6_dynamic_universe.routing import strategy_route
from rc6_family_readiness import normalize_family
from rc6_ppi_future_contract_policy import (
    standard_dlr_future_evidence, standard_dlr_terms,
)


AT = datetime(2026, 11, 2, 17, 15, tzinfo=timezone.utc)


class SourceAliasBoundaryTest(unittest.TestCase):
    def test_on_contract_namespace_is_distinct_from_catalog_namespace(self):
        for alias in ("ON", "OBL.NEG.", "OBL.PYME", "OBLIGACIONES_NEGOCIABLES"):
            with self.subTest(alias=alias):
                self.assertEqual(sources.FAMILIES[alias], "OBLIGACIONES")
        self.assertEqual(family_name("ON"), "OBLIGACIONES")
        self.assertEqual(canonical_family("OBLIGACIONES"), "ON")
        route = strategy_route("ON")
        self.assertEqual((route["family"], route["engine"]),
                         ("OBLIGACIONES", "FIXED_INCOME_ANALYTICS"))
        self.assertFalse(route["entry_authority"])

        # A raw ON snapshot may not silently satisfy an OBLIGACIONES request
        # before an explicit, identity-preserving namespace reconciliation.
        ppi = SourceSnapshot("PPI", "ON", AT, {"last": 100}, True)
        result = select_live_snapshot("OBLIGACIONES", {"PPI": ppi})
        self.assertEqual((result.status, result.reason),
                         ("HOLD_DATA_SOURCE", "PRIMARY_SOURCE_FAMILY_MISMATCH:PPI"))

    def test_lebac_and_nobac_remain_auditable_separately_from_letras(self):
        for family in ("LEBAC", "NOBAC"):
            with self.subTest(family=family):
                self.assertEqual(normalize_family(family), family)
                self.assertEqual(family_name(family), "LETRAS")
                route = strategy_route(family)
                self.assertEqual((route["family"], route["engine"]),
                                 ("LETRAS", "FIXED_INCOME_ANALYTICS"))
                self.assertFalse(route["entry_authority"])
                self.assertEqual(history_collection_capability(
                    family, status="AVAILABLE"), "UNSUPPORTED_HISTORY_FAMILY")

    def test_etf_byma_identity_does_not_absorb_foreign_exchange_candidate(self):
        self.assertEqual(family_name("ETF"), "ETFS")
        self.assertEqual(sources.FAMILIES["ETF"], "ETFS")
        byma = {"ticker": "SPY", "instrument_type": "ETF", "market": "BYMA",
                "currency": "ARS", "settlement": "A-24HS"}
        self.assertEqual(sources._canonical_identity(byma),
                         ["SPY", "ETFS", "BYMA", "ARS", "A-24HS"])
        for foreign in ("NYSE", "NASDAQ"):
            with self.subTest(market=foreign), self.assertRaisesRegex(
                    ValueError, "EXACT_IDENTITY_REQUIRED"):
                sources._canonical_identity({**byma, "market": foreign})
        route = strategy_route("ETF")
        self.assertEqual(route["engine"], "EQUITY_SPOT")
        self.assertFalse(route["entry_authority"])

    def test_fci_local_and_exterior_keep_separate_contract_scope(self):
        self.assertEqual(sources.FAMILIES["FCI_LOCAL"], "FCI")
        self.assertEqual(canonical_family("FCI"), "FCI_LOCAL")
        self.assertEqual(canonical_family("FCI_EXTERIOR"), "FCI_EXTERIOR")
        self.assertIn("FCI_EXTERIOR", FAMILY_CONTRACT_FIELDS)
        self.assertIn("FCI_LOCAL", FAMILY_CONTRACT_FIELDS)
        self.assertNotEqual(FAMILY_CONTRACT_FIELDS["FCI_EXTERIOR"],
                            FAMILY_CONTRACT_FIELDS["FCI_LOCAL"])

        # Generic family_name collapses the exterior name, but the strategy
        # router is observation-only and cannot invent a foreign-fund adapter.
        self.assertEqual(family_name("FCI_EXTERIOR"), "FCI")
        route = strategy_route("FCI_EXTERIOR")
        self.assertEqual((route["family"], route["engine"]), ("FCI", "FUND_NAV"))
        self.assertFalse(route["entry_authority"])
        self.assertEqual(route["status"], "OBSERVE_ONLY")
        self.assertEqual(strategy_route("FCI_LOCAL")["engine"], "UNSUPPORTED")

    def test_acciones_usa_generic_alias_collapse_never_grants_entry(self):
        self.assertEqual(canonical_family("ACCIONES_USA"), "ACCIONES_USA")
        self.assertIn("ACCIONES_USA", FAMILY_CONTRACT_FIELDS)
        self.assertEqual(family_name("ACCIONES_USA"), "ACCIONES")
        for scalping in (False, True):
            with self.subTest(scalping=scalping):
                route = strategy_route("ACCIONES_USA", scalping=scalping)
                self.assertEqual(route["family"], "ACCIONES")
                self.assertEqual(route["engine"],
                                 "SCALPING" if scalping else "EQUITY_SPOT")
                self.assertEqual(route["status"], "SHADOW")
                self.assertFalse(route["entry_authority"])

    def test_exact_2026_monthly_dlr_only_and_different_expiry(self):
        oct_terms = standard_dlr_terms("DLR/OCT26")
        nov_terms = standard_dlr_terms("DLR/NOV26")
        dec_terms = standard_dlr_terms("DLR/DIC26")
        self.assertEqual(oct_terms["expires_at"][:10], "2026-10-30")
        self.assertEqual(nov_terms["expires_at"][:10], "2026-11-30")
        self.assertEqual(dec_terms["expires_at"][:10], "2026-12-30")
        self.assertEqual(len({oct_terms["expires_at"], nov_terms["expires_at"],
                              dec_terms["expires_at"]}), 3)
        record = {"ticker": "DLR/NOV26", "instrument_type": "FUTUROS",
                  "status": "AVAILABLE", "market": "A3", "currency": "ARS",
                  "settlement": "INMEDIATA",
                  "capability": "NEEDS_FUTURES_MARGIN_AND_CONTRACT",
                  "metadata_json": {"_discovery_source": "PPI_PRIMARY"}}
        terms = standard_dlr_future_evidence(record)
        self.assertIsNotNone(terms)
        self.assertEqual(terms["series_policy"], "STANDARD_MONTHLY_DLR_2026_ONLY")
        self.assertEqual(terms["paper_side_scope"], "LONG_ONLY_V1")
        self.assertEqual(terms["settlement_price_rule"],
                         "REQUIRES_DESIGNATED_SOURCE_RULE_PER_SERIES_AND_DATE")
        for ticker in ("DLR/NOV26M", "DLR/OCT26-DLR/NOV26", "DLR/ENE27",
                       "RFX20/NOV26", "SOJA/NOV26"):
            with self.subTest(ticker=ticker):
                self.assertIsNone(standard_dlr_terms(ticker))
                self.assertIsNone(standard_dlr_future_evidence({**record, "ticker": ticker}))
        self.assertIsNone(standard_dlr_future_evidence({
            **record, "metadata_json": {"_discovery_source": "IOL"}}))

    def test_iol_only_cannot_replace_missing_or_stale_ppi_primary(self):
        families = ("ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS",
                    "OBLIGACIONES", "ON", "OPCIONES", "FUTUROS",
                    "CAUCIONES", "FCI", "FCI_LOCAL", "FCI_EXTERIOR")
        for family in families:
            with self.subTest(family=family):
                self.assertEqual(primary_live_source(family), "PPI")
                iol = SourceSnapshot("IOL", family, AT, {"last": 100}, True)
                missing = select_live_snapshot(family, {"IOL": iol})
                self.assertEqual((missing.status, missing.reason),
                                 ("HOLD_DATA_SOURCE", "PRIMARY_SOURCE_MISSING:PPI"))
                stale = SourceSnapshot("PPI", family, AT, {"last": 100}, False)
                blocked = select_live_snapshot(family, {"PPI": stale, "IOL": iol})
                self.assertEqual((blocked.status, blocked.reason),
                                 ("HOLD_DATA_SOURCE", "PRIMARY_SOURCE_STALE:PPI"))

    def test_licitaciones_and_canjes_contract_evidence_is_not_paper_route(self):
        for family in ("LICITACIONES", "CANJES"):
            with self.subTest(family=family):
                self.assertIn(family, FAMILY_CONTRACT_FIELDS)
                route = strategy_route(family)
                self.assertEqual(route["engine"], "UNSUPPORTED")
                self.assertFalse(route["entry_authority"])
        self.assertEqual(history_collection_capability(
            "LICITACIONES", status="AVAILABLE"), "OUT_OF_SCOPE_READONLY_LEGACY")

    def test_scalping_is_spot_strategy_style_not_new_instrument_family(self):
        for family in ("ACCIONES", "CEDEARS", "ETFS"):
            with self.subTest(family=family):
                route = strategy_route(family, scalping=True)
                self.assertEqual(route["engine"], "SCALPING")
                self.assertFalse(route["entry_authority"])
        for family in ("BONOS", "OPCIONES", "FUTUROS", "FCI"):
            with self.subTest(family=family):
                route = strategy_route(family, scalping=True)
                self.assertNotEqual(route["engine"], "SCALPING")
                self.assertFalse(route["entry_authority"])


if __name__ == "__main__":
    unittest.main()
