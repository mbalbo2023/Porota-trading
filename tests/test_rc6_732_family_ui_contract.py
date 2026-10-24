"""Bounded UI contract regressions for the RC6 #732 findings."""
import unittest
import re
from unittest.mock import patch

import da_dashboard_ux_hf6 as legacy_ux
import bg_paper_dashboard as legacy_dashboard
from er_dashboard_truth_projection_rc6 import normalize_family
from rc6_trader_dashboard import components, view_common, views_analytics, views_instruments, views_trading, views_universe
from rc6_trader_dashboard.projection import Page


class FamilyUiContractTests(unittest.TestCase):
    def test_repeated_identity_has_distinct_row_dialog_targets(self):
        identity = '["AL30","BONOS","BYMA","ARS","A-24HS"]'
        page = Page("market_snapshots", [
            {"identity": identity, "symbol": "AL30", "last": "100"},
            {"identity": identity, "symbol": "AL30", "last": "101"},
        ], 2, "AVAILABLE")
        markup = components.table(page, "Mercado", components.fields("symbol|Ticker;last|Último"),
                                  table_id="market")
        row_ids = re.findall(r"<tr id='([^']+)' data-row='true'>", markup)
        self.assertEqual(len(row_ids), 2)
        self.assertEqual(len(set(row_ids)), 2)
        for row_id in row_ids:
            self.assertIn(f"data-detail-dialog='{row_id}-dialog'", markup)
            self.assertIn(f"<dialog id='{row_id}-dialog'", markup)

    def test_all_ten_paper_families_visible_without_claiming_readiness(self):
        canonical = {
            "ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS",
            "OBLIGACIONES", "OPCIONES", "FUTUROS", "CAUCIONES", "FCI",
        }
        self.assertEqual(legacy_ux.OPERATIONAL_FAMILIES, canonical)
        legacy_ux.assert_ux_invariants()
        self.assertIn("ETFS", legacy_ux.families_for_group("etf"))
        self.assertIn("OBLIGACIONES", legacy_ux.families_for_group("on"))
        self.assertIn("OBLIGACIONES", views_trading.FAMILY_GROUPS["renta-fija"])
        self.assertIn("metadata.nominal_unit", [f.key for f in views_instruments.family_fields("OBLIGACIONES")])

    def test_legacy_aliases_do_not_duplicate_family_rows_or_totals(self):
        for group in legacy_ux.FAMILY_GROUPS:
            families = [normalize_family(family) for family in legacy_ux.families_for_group(group)]
            self.assertEqual(len(families), len(set(families)), group)
        truth = {"readiness": {"families": [{"family": "OBLIGACIONES", "runtime_ready": 3,
                                                  "candidate_total": 5, "catalog_total": 5}]},
                 "contract": {"families": []}}
        rows = legacy_dashboard._family_ux_snapshot(
            legacy_ux.families_for_group("on"), truth=truth, paper_history={})
        self.assertEqual([(row["family"], row["ready"]) for row in rows], [("OBLIGACIONES", 3)])

    def test_catalog_state_explains_its_limited_authority(self):
        readiness = next(f for f in view_common.CATALOG if f.key == "readiness")
        self.assertIn("catálogo", readiness.label)
        self.assertIn("no apertura PAPER", readiness.label)
        self.assertEqual(components.value_html({"readiness": "RUNTIME_READY"}, readiness),
                         components.badge("RUNTIME_READY"))
        class Ficha:
            filters = {"identity": '["YPF","OBLIGACIONES","BYMA","ARS","A-24HS"]'}

            def catalog(self):
                return Page("catalog", [{"symbol": "YPF", "family": "OBLIGACIONES",
                                         "readiness": "RUNTIME_READY", "market": "BYMA",
                                         "currency": "ARS", "settlement": "A-24HS",
                                         "identity": self.filters["identity"]}], 1, "AVAILABLE")

        ficha = views_instruments.render(Ficha(), None, "ficha")
        self.assertIn("No acredita contrato completo", ficha)
        self.assertIn("Convención nominal", ficha)

    def test_market_requires_exact_identity_without_source_failure_message(self):
        page = Page("market_snapshots", reason="SELECT_EXACT_IDENTITY_FOR_MARKET")
        markup = components.table(page, "Mercado", components.fields("symbol|Ticker"), table_id="market")
        self.assertIn("REQUIERE_IDENTIDAD", markup)
        self.assertNotIn("NO_VERIFICADO · SELECT_EXACT_IDENTITY_FOR_MARKET", markup)
        self.assertNotIn("data-status='NO_VERIFICADO'", markup)
        real_error = components.table(Page("market_snapshots", reason="CATALOG_READ_ERROR"),
                                      "Mercado", components.fields("symbol|Ticker"), table_id="market")
        self.assertIn("NO_VERIFICADO · CATALOG_READ_ERROR", real_error)

    def test_shadow_family_channel_is_explicit(self):
        page = Page("committed SHADOW", [{"family": "OBLIGACIONES", "strategy": "OBSERVE_ONLY"}], 1, "AVAILABLE")
        with patch.object(views_universe, "shadow_rows", return_value=page), \
             patch.object(views_universe, "render_table", return_value="rendered") as render:
            self.assertEqual(views_universe.render(None, None, "familias"), "rendered")
        self.assertIn("SHADOW experimental", render.call_args.kwargs["note"])
        self.assertIn("observer PAPER", render.call_args.kwargs["note"])
        fallback = Page("financial_instrument_catalog", [{"family": "BONOS"}], 1, "AVAILABLE")
        with patch.object(views_universe, "shadow_rows", return_value=Page("SHADOW")), \
             patch.object(views_universe, "family_summary", return_value=fallback), \
             patch.object(views_universe, "render_table", return_value="rendered") as render:
            views_universe.render(None, None, "familias")
        self.assertIn("sin corte SHADOW", render.call_args.kwargs["note"])

    def test_signal_cohort_does_not_claim_instrument_score(self):
        page = Page("committed SHADOW", [], 0, "AVAILABLE")
        with patch.object(views_analytics, "shadow_rows", return_value=page), \
             patch.object(views_analytics, "render_table", return_value="rendered") as render:
            self.assertEqual(views_analytics.render(None, None, "senales"), "rendered")
        columns = render.call_args.args[5]
        details = render.call_args.args[6]
        self.assertIn("score_bucket", [f.key for f in columns])
        self.assertNotIn("score", [f.key for f in columns])
        self.assertNotIn("symbol", [f.key for f in details])
        self.assertIn("AUC", render.call_args.kwargs["note"])


if __name__ == "__main__":
    unittest.main()
