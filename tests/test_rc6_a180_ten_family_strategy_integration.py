"""A180 PAPER-family strategy wiring: no generic equity BUY fallback.

These tests run with ``python -m unittest`` as well as pytest.  A complete
predeploy must also exercise all native safety and ledger tests, including the
external policy and the exact frozen candidate.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from be_paper_engine import D, PaperBroker, PaperStore, Quote
from bs_instrument_contracts import InstrumentContract


NOW = datetime.fromisoformat("2026-10-29T11:30:00-03:00")


def stamp(offset_seconds=0):
    return (NOW + timedelta(seconds=offset_seconds)).isoformat()


def option_quote():
    contract = InstrumentContract(
        "GGALC100OC", "OPCIONES", "ARS", "BYMA", "INMEDIATA",
        D("100"), D("1"), "CONTRACT_EVIDENCE_V2_BOUND",
        expires_at=stamp(10 * 86400), underlying="GGAL",
        strike=D("100"), option_right="CALL")
    return Quote(
        "GGALC100OC", "OPCIONES", "INMEDIATA", D("1"), D("0.95"), D("1"),
        D("20"), D("20"), stamp(), contract=contract,
        currency="ARS", market="BYMA", metadata_source="PPI_CATALOG:2026-10-29",
        book_at=stamp(), trade_at=stamp(), last_kind="TRADE")


class FamilyIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rc6-a180-family-")
        self.addCleanup(self.temp.cleanup)
        self.store = PaperStore(str(Path(self.temp.name) / "paper.db"))
        self.broker = PaperBroker(
            self.store, initial_cash="1000000", risk_pct="0.01",
            max_position_pct="0.25", max_total_exposure_pct="1",
            daily_loss_pct=None, ai_mode="OFF", require_ai=False,
            clock_fn=stamp)

    def seed_underlying(self, *, duplicate_fresh=False, missing_ppi=False,
                        underlying_block=""):
        with self.store.connect() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS financial_instrument_catalog (
                ticker TEXT, instrument_type TEXT, market TEXT, currency TEXT,
                settlement TEXT, status TEXT, capability TEXT)""")
            c.execute("""CREATE TABLE IF NOT EXISTS candidate_identity_v2 (
                ticker TEXT, instrument_type TEXT, market TEXT, currency TEXT,
                settlement TEXT, status TEXT, can_simulate INTEGER)""")
            terms = ["A-24HS"] + (["INMEDIATA"] if duplicate_fresh else [])
            for settlement in terms:
                key = ("GGAL", "ACCIONES", "BYMA", "ARS", settlement)
                c.execute("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?)",
                          key + ("AVAILABLE", "READY_PAPER_SPOT"))
                c.execute("INSERT INTO candidate_identity_v2 VALUES(?,?,?,?,?,?,?)",
                          key + ("AVAILABLE", 1))
        # Eight distinct causal trade prints over seven minutes; not eight
        # duplicate HTTP receipt timestamps of one stale market observation.
        prices = [D("98"), D("98.2"), D("98.45"), D("98.7"), D("99"),
                  D("99.3"), D("99.65"), D("100")]
        for settlement in terms:
            for idx, price in enumerate(prices):
                at = stamp((idx - 7) * 60)
                self.store.add_quote(Quote(
                    "GGAL", "ACCIONES", settlement, price, D("99.95"), D("100.05"),
                    D("500"), D("500"), at, currency="ARS", market="BYMA",
                    metadata_source=("UNVERIFIED" if missing_ppi
                                     else "PPI_CATALOG:2026-10-29"),
                    opening_block_reason=underlying_block,
                    book_at=at, trade_at=at, last_kind="TRADE"))

    def test_option_is_hold_without_underlying_not_equity_buy(self):
        action, score, reason, features = self.broker.decide(option_quote())
        self.assertEqual(action, "HOLD")
        self.assertEqual(reason, "OPTION_UNDERLYING_EVIDENCE_MISSING")
        self.assertEqual(features["family"], "OPCIONES")
        self.assertNotIn("sma3", features)

    def test_option_long_can_form_candidate_only_with_exact_fresh_ppi(self):
        self.seed_underlying()
        action, score, reason, features = self.broker.decide(option_quote())
        self.assertEqual((action, reason), ("BUY", "OPTION_LONG_PAPER_CANDIDATE"))
        self.assertGreater(score, D("0"))
        self.assertTrue(features["paper_candidate_only"])
        self.assertFalse(features["entry_authority"])
        self.assertEqual(features["real_routes_used"], [])
        self.assertIn("IV_NO_VERIFICADO", features["warnings"])
        self.assertEqual(self.store.open_positions(), [])

    def test_option_ambiguous_underlying_settlement_is_hold(self):
        self.seed_underlying(duplicate_fresh=True)
        action, _, reason, _ = self.broker.decide(option_quote())
        self.assertEqual((action, reason),
                         ("HOLD", "OPTION_UNDERLYING_EVIDENCE_MISSING"))

    def test_option_underlying_missing_ppi_source_is_hold(self):
        self.seed_underlying(missing_ppi=True)
        action, _, reason, _ = self.broker.decide(option_quote())
        self.assertEqual((action, reason),
                         ("HOLD", "OPTION_UNDERLYING_EVIDENCE_MISSING"))

    def test_unverified_older_underlying_print_cannot_be_laundered_by_latest_ppi(self):
        self.seed_underlying()
        older = stamp(-240)
        self.store.add_quote(Quote(
            "GGAL", "ACCIONES", "A-24HS", D("99"), D("99.95"), D("100.05"),
            D("500"), D("500"), older, currency="ARS", market="BYMA",
            metadata_source="UNVERIFIED_COMPLEMENTARY_REPLAY",
            book_at=older, trade_at=older, last_kind="TRADE"))
        # The current snapshot itself remains PPI-backed and fresh. An older
        # replaced trade must nevertheless veto the complete candidate.
        self.store.add_quote(Quote(
            "GGAL", "ACCIONES", "A-24HS", D("100"), D("99.95"), D("100.05"),
            D("500"), D("500"), stamp(), currency="ARS", market="BYMA",
            metadata_source="PPI_CATALOG:2026-10-29",
            book_at=stamp(), trade_at=stamp(), last_kind="TRADE"))
        action, _, reason, _ = self.broker.decide(option_quote())
        self.assertEqual((action, reason),
                         ("HOLD", "OPTION_UNDERLYING_EVIDENCE_MISSING"))

    def test_invalid_later_trade_revision_cannot_resurrect_old_event(self):
        self.seed_underlying()
        older = stamp(-240)
        self.store.add_quote(Quote(
            "GGAL", "ACCIONES", "A-24HS", D("0"), D("0"), D("0"),
            D("0"), D("0"), older, currency="ARS", market="BYMA",
            metadata_source="UNVERIFIED_TOMBSTONE",
            book_at=older, trade_at=older, last_kind="UNAVAILABLE"))
        self.store.add_quote(Quote(
            "GGAL", "ACCIONES", "A-24HS", D("100"), D("99.95"), D("100.05"),
            D("500"), D("500"), stamp(), currency="ARS", market="BYMA",
            metadata_source="PPI_CATALOG:2026-10-29",
            book_at=stamp(), trade_at=stamp(), last_kind="TRADE"))
        action, _, reason, _ = self.broker.decide(option_quote())
        self.assertEqual((action, reason),
                         ("HOLD", "OPTION_UNDERLYING_EVIDENCE_MISSING"))

    def test_option_underlying_catalog_opening_block_is_hold(self):
        self.seed_underlying(underlying_block="PPI_FRESHNESS_STALE")
        action, _, reason, _ = self.broker.decide(option_quote())
        self.assertEqual((action, reason),
                         ("HOLD", "OPTION_UNDERLYING_EVIDENCE_MISSING"))

    def test_option_signal_cannot_exceed_evaluated_single_contract(self):
        self.seed_underlying()
        action, score, reason, features = self.broker.decide(option_quote())
        self.assertEqual(action, "BUY", reason)
        # This is a synthetic isolated PAPER store; the public account sizer
        # still has to respect the pure signal's single-contract maximum.
        execution = PaperBroker(
            self.store, initial_cash="1000000", risk_pct="0.01",
            max_position_pct="0.25", max_total_exposure_pct="1",
            daily_loss_pct="2.5", daily_soft_stop_pct="1.5",
            ai_mode="OFF", require_ai=False, clock_fn=stamp)
        opened, why, paper_id = execution._open(option_quote(), score, features)
        self.assertTrue(opened, why)
        self.assertIsNotNone(paper_id)
        row = execution.store.open_positions()[0]
        self.assertEqual(D(row["quantity"]), D("1"))
        self.assertEqual(row["asset_class"], "OPCIONES")
        with self.store.connect() as c:
            sides = [r[0] for r in c.execute(
                "SELECT side FROM paper_fills WHERE paper_id=?", (paper_id,))]
        self.assertEqual(sides, ["BUY_SIMULATED"])

    def test_option_runtime_decision_lineage_is_not_equity_momentum(self):
        self.seed_underlying()
        broker = PaperBroker(
            self.store, initial_cash="1000000", risk_pct="0.01",
            max_position_pct="0.25", max_total_exposure_pct="1",
            daily_loss_pct="2.5", daily_soft_stop_pct="1.5",
            ai_mode="OFF", require_ai=False, clock_fn=stamp)
        broker.on_quote(option_quote())
        with self.store.connect() as c:
            row = c.execute("""SELECT decision_key,features_json FROM paper_decisions
                WHERE symbol='GGALC100OC' ORDER BY id DESC LIMIT 1""").fetchone()
        self.assertIsNotNone(row)
        self.assertTrue(row[0].startswith("OPTION_LONG_UNDERLYING_TREND_PAPER_V1:"))
        features = json.loads(row[1])
        self.assertEqual(features["performance_lineage"]["strategy_id"],
                         "OPTION_LONG_UNDERLYING_TREND_PAPER_V1")

    def test_canonical_book_rejects_option_without_atomic_recorded_buy(self):
        self.seed_underlying()
        action, score, _, features = self.broker.decide(option_quote())
        self.assertEqual(action, "BUY")
        broker = PaperBroker(
            self.store, initial_cash="1000000", risk_pct="0.01",
            max_position_pct="0.25", max_total_exposure_pct="1",
            daily_loss_pct="2.5", daily_soft_stop_pct="1.5",
            ai_mode="OFF", require_ai=False, clock_fn=stamp,
            admission_guard=lambda _quote, _store: (True, ""))
        with (patch("cg_paper_workspace.database_path", return_value=Path(self.store.path)),
              patch("rc6_paper_admission_gate.admission_status", return_value=(True, ""))):
            opened, reason, _ = broker._open(option_quote(), score, features)
            self.assertFalse(opened)
            self.assertEqual(reason, "PAPER_SPECIALIZED_DECISION_KEY_MISMATCH")
            self.assertEqual(self.store.open_positions(), [])
            # The native on_quote path records its strategy-identified BUY
            # before entering the locked ledger, unlike the direct bypass.
            broker.on_quote(option_quote())
            rows = broker.store.open_positions()
            self.assertEqual(len(rows), 1)
            self.assertEqual(D(rows[0]["quantity"]), D("1"))

    def test_fi_no_provider_never_inherits_equity_buy(self):
        contract = InstrumentContract(
            "AL30", "BONOS", "ARS", "BYMA", "A-24HS", D("0.01"),
            D("1"), "PPI_CATALOG:FI")
        q = Quote("AL30", "BONOS", "A-24HS", D("100"), D("99"), D("100"),
                  D("300"), D("300"), stamp(), contract=contract,
                  currency="ARS", market="BYMA", metadata_source="PPI_CATALOG:FI",
                  book_at=stamp(), trade_at=stamp(), last_kind="TRADE")
        action, _, reason, features = self.broker.decide(q)
        self.assertEqual(action, "HOLD")
        self.assertNotIn("sma3", features)
        self.assertNotIn("Momentum positivo", reason)

    def test_future_routing_keeps_owned_engine(self):
        from be_paper_engine import PaperBroker
        self.assertTrue(callable(PaperBroker._on_future_quote))
        self.assertTrue(callable(PaperBroker._decide_future))

    def test_all_ten_shadow_routes_still_have_no_entry_authority(self):
        from rc6_dynamic_universe.routing import strategy_route
        for family in ("ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS",
                       "OBLIGACIONES", "OPCIONES", "FUTUROS", "CAUCIONES", "FCI"):
            with self.subTest(family=family):
                self.assertFalse(strategy_route(family)["entry_authority"])


if __name__ == "__main__":
    unittest.main()
