"""A658 successor: ETFs enter normal PAPER screening, with strict native-data gates.

All instruments, quotes and clocks below are synthetic.  These tests never
contact PPI, touch the operational droplet or call an actual order route.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import be_paper_engine as engine
import bu_instrument_catalog as catalog
import cf_intraday_scalping as scalping
from rc6_dynamic_universe.routing import dispatch_observation, strategy_route


AT = datetime.fromisoformat("2026-10-05T10:46:00-03:00")


def synthetic_quote(family="ETFS"):
    return engine.Quote(
        "SYNTHETIC_ETF" if family in {"ETFS", "ETF"} else "SYNTHETIC_EQ",
        family, "A-24HS", Decimal("107.5"), Decimal("107.5"),
        Decimal("107.55"), Decimal("1000"), Decimal("1000"),
        AT.isoformat(), currency="ARS", market="BYMA",
        metadata_source="A658_SYNTHETIC_NOT_PPI", book_at=AT.isoformat(),
        trade_at=AT.isoformat(), last_kind="TRADE",
    )


def synthetic_record(family="ETFS"):
    return {"ticker": "SYNTHETIC_ETF", "instrument_type": family,
            "market": "BYMA", "currency": "ARS", "settlement": "A-24HS",
            "status": "AVAILABLE", "capability": "READY_PAPER_SPOT"}


class A658EtfOperatorHoldTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="rc6-a658-etf-hold-")
        self.addCleanup(folder.cleanup)
        self.store = engine.PaperStore(str(Path(folder.name) / "synthetic.db"))
        catalog.init_schema(self.store)
        scalping.init_schema(self.store)
        self.broker = engine.PaperBroker(self.store, clock_fn=lambda: AT.isoformat(), ai_mode="OFF")

    def assert_no_paper_entry(self):
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM paper_positions WHERE status='OPEN'").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT real_orders_sent FROM observer_state WHERE id=1").fetchone()[0], 0)

    def test_etf_and_alias_reach_native_gate_without_auto_buy(self):
        for family in ("ETFS", "ETF"):
            with self.subTest(family=family), patch.object(
                    self.broker, "admission_error", return_value="PPI_BOOK_UNVERIFIED_TEST") as gate:
                action, score, reason, features = self.broker.decide(synthetic_quote(family))
                self.assertEqual((action, score, reason),
                                 ("HOLD", Decimal("0"), "PPI_BOOK_UNVERIFIED_TEST"))
                self.assertFalse(features.get("entry_authority", False))
                gate.assert_called_once()
        self.assert_no_paper_entry()
    def test_direct_etf_paper_admission_still_requires_native_source(self):
        # The preopen guard alone is mocked; native evidence is still mandatory.
        # No source, economics or actual PAPER order route is mocked into success.
        with patch.dict(os.environ, {"POROTA_OPERATIONAL_FAMILIES": "ACCIONES,CEDEARS,ETFS"}), \
             patch.object(self.broker, "_preopen_admission_error", return_value=None), \
             patch.object(self.broker, "admission_error", return_value="PPI_BOOK_UNVERIFIED_TEST") as gate:
            opened, reason, paper_id = self.broker.admit_paper_candidate(
                synthetic_quote(), Decimal("0.95"), {"synthetic": True})
        self.assertEqual((opened, reason, paper_id), (False, "PPI_BOOK_UNVERIFIED_TEST", None))
        gate.assert_called_once()
        self.assert_no_paper_entry()
    def test_scalping_etf_without_native_inputs_stays_hold(self):
        record = synthetic_record()
        action = scalping.evaluate_candidate(self.store, record, at=AT)
        self.assertEqual(action, "HOLD")
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT action,reason FROM scalping_candidates ORDER BY id DESC LIMIT 1").fetchone()
            self.assertEqual(row["action"], "HOLD")
            self.assertNotEqual(row["reason"], "ETF_OPERATOR_HOLD")
            self.assertTrue(row["reason"])
        self.assert_no_paper_entry()
    def test_scalping_active_etf_rechecks_session_before_broker(self):
        with patch.dict(os.environ, {"PAPER_SCALPING_MODE": "ACTIVE_PAPER"}), \
             patch.object(scalping, "_market_open", return_value=False) as session:
            result = scalping.promote_paper_candidate(self.store, synthetic_record(), at=AT)
        self.assertEqual(result, "OUTSIDE_SCALPING_PAPER_SESSION")
        session.assert_called_once()
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM paper_decisions").fetchone()[0], 0)
        self.assert_no_paper_entry()
    def test_dynamic_route_allows_etf_screening_but_not_entry(self):
        for family in ("ETF", "ETFS"):
            for scalp in (False, True):
                route = strategy_route(family, scalping=scalp)
                self.assertEqual(route["status"], "SHADOW")
                self.assertNotIn("ETF_OPERATOR_HOLD", route["reason_codes"])
                self.assertFalse(route["entry_authority"])
                self.assertTrue(route["generic_equity"])
        delivered = []
        result = dispatch_observation(
            synthetic_record(), {"EQUITY_SPOT": lambda row, *, as_of: delivered.append(row) or "OBSERVED"},
            as_of=AT,
        )
        self.assertEqual(result["handler_result"], "OBSERVED")
        self.assertEqual(len(delivered), 1)
        self.assertFalse(result["entry_authority"])
        from rc6_operator_family_hold import paper_entry_hold_reason
        self.assertEqual(paper_entry_hold_reason("ETF"), "")
        self.assertEqual(paper_entry_hold_reason("ETFS"), "")
    def test_other_families_and_existing_etf_exit_path_unaffected(self):
        for family in ("ACCIONES", "CEDEARS"):
            with self.subTest(family=family):
                self.assertEqual(strategy_route(family)["status"], "SHADOW")
                self.assertNotIn("ETF_OPERATOR_HOLD", strategy_route(family)["reason_codes"])
                with patch.object(self.broker, "_preopen_admission_error", return_value=None), \
                     patch.object(self.broker, "admission_error", return_value="SYNTHETIC_OTHER_FAMILY_HOLD"):
                    result = self.broker.admit_paper_candidate(synthetic_quote(family), Decimal("0.95"), {})
                self.assertEqual(result, (False, "SYNTHETIC_OTHER_FAMILY_HOLD", None))
        for family in ("BONOS", "LETRAS", "OBLIGACIONES", "OPCIONES", "FUTUROS", "CAUCIONES", "FCI"):
            self.assertNotIn("ETF_OPERATOR_HOLD", strategy_route(family)["reason_codes"])
        # Existing ETF inventory must be monitored for EXIT before we decide
        # whether any *new* ETF opening is allowed. Avoid mocking an actual fill.
        quote = synthetic_quote()
        with patch.object(self.broker, "_maybe_close", return_value=True) as exit_supervisor, \
             patch.object(self.broker, "decide", side_effect=AssertionError("OPEN_BEFORE_EXIT")):
            self.broker.on_quote(quote)
        exit_supervisor.assert_called_once_with(quote)
        self.assert_no_paper_entry()
