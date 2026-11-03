"""A621: integration guard prevents generic spot callback for FI SELL."""
import sqlite3
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bm_exit_supervisor import PositionExitSupervisor
import test_rc6_fi_sell_persisted_evidence as fixtures

AT = fixtures.AT
BOOK1_AT = fixtures.BOOK1_AT


class FakeQuote:
    symbol = "AL30"
    asset_class = "BONOS"
    settlement = "INMEDIATA"
    currency = "ARS"
    market = "BYMA"
    bid = Decimal("100")
    ask = Decimal("101")
    bid_size = Decimal("100")
    book_at = BOOK1_AT

    def monetary_identity(self):
        return self.currency, self.market

    def time_error(self, at, max_age_seconds):
        return ""


class SupervisorIntegrationTests(unittest.TestCase):
    setUp = fixtures.PersistedEvidenceTests.setUp
    contract = fixtures.PersistedEvidenceTests.contract

    def _run_supervisor(self, *, asset_class="BONOS", audit=None, audit_error=None):
        with self.store.connect() as db:
            db.execute("UPDATE paper_positions SET asset_class=?",(asset_class,))
        with self.store.connect() as db:
            p=dict(db.execute("SELECT * FROM paper_positions WHERE paper_id='PAPER-FI-1'").fetchone())
        q = FakeQuote()
        q.asset_class = asset_class
        close = Mock(side_effect=AssertionError("generic close callback must not run"))
        broker=SimpleNamespace(store=self.store, participation=Decimal("1"),
                               quote_max_age_seconds=300)
        supervisor=PositionExitSupervisor(broker,clock_fn=lambda:AT,close_fn=close)
        with patch("rc6_fi_sell_persisted_evidence.inspect_persisted_fi_exit",
                   return_value=audit, side_effect=audit_error):
            verdict=supervisor.supervise(p,q,AT)
        return verdict,close

    def test_bonos_due_does_not_call_generic_spot_close(self):
        audit={"blockers": ["FI_EXIT_DECISION_POSTCOMMIT_SIGNED_RECEIPT_MISSING",
                            "FI_EXIT_ALYC_ACCOUNT_TARIFF_UNVERIFIED"]}
        verdict,close=self._run_supervisor(audit=audit)
        self.assertEqual(verdict.state,"EXIT_PENDING_FI_NATIVE_EVIDENCE")
        self.assertIn("FI_EXIT_DECISION",verdict.reason)
        close.assert_not_called()
        with self.store.connect() as db:
            intent=db.execute("SELECT * FROM paper_exit_intents WHERE paper_id='PAPER-FI-1'").fetchone()
            self.assertEqual(intent["state"],"EXIT_PENDING_FI_NATIVE_EVIDENCE")
            self.assertEqual(intent["cause"],"EOD_PAPER")
            self.assertEqual(db.execute("SELECT status FROM paper_positions WHERE paper_id='PAPER-FI-1'").fetchone()[0],"OPEN")

    def test_repeated_due_exit_is_idempotently_unfilled(self):
        audit={"blockers": ["FI_EXIT_DECISION_POSTCOMMIT_SIGNED_RECEIPT_MISSING"]}
        first,close=self._run_supervisor(audit=audit)
        second,close2=self._run_supervisor(audit=audit)
        self.assertEqual((first.state,second.state),
                         ("EXIT_PENDING_FI_NATIVE_EVIDENCE",)*2)
        close.assert_not_called()
        close2.assert_not_called()
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT attempts FROM paper_exit_intents WHERE paper_id='PAPER-FI-1'").fetchone()[0],0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM paper_fills WHERE side='SELL_SIMULATED'").fetchone()[0],0)
            self.assertEqual(db.execute("SELECT status FROM paper_positions WHERE paper_id='PAPER-FI-1'").fetchone()[0],"OPEN")

    def test_obligaciones_due_does_not_call_generic_spot_close(self):
        verdict,close=self._run_supervisor(asset_class="OBLIGACIONES",
                                           audit={"blockers":["FI_EXIT_PPI_BOOK_PRICE_PER100_UNVERIFIED"]})
        self.assertEqual(verdict.state,"EXIT_PENDING_FI_NATIVE_EVIDENCE")
        close.assert_not_called()

    def test_audit_exception_blocks_and_preserves_position(self):
        verdict,close=self._run_supervisor(audit_error=OSError("not found"))
        self.assertEqual(verdict.state,"WATCH_FI_NATIVE_EVIDENCE_BLOCKED")
        self.assertIn("FI_EXIT_PERSISTED_AUDIT_UNAVAILABLE:OSError",verdict.reason)
        close.assert_not_called()

    def test_missing_quote_never_generates_fill(self):
        with self.store.connect() as db:
            p=dict(db.execute("SELECT * FROM paper_positions WHERE paper_id='PAPER-FI-1'").fetchone())
        close=Mock(side_effect=AssertionError("must not call"))
        broker=SimpleNamespace(store=self.store,participation=Decimal("1"),quote_max_age_seconds=300)
        supervisor=PositionExitSupervisor(broker,clock_fn=lambda:AT,close_fn=close)
        verdict=supervisor.supervise(p,None,AT)
        self.assertEqual(verdict.state,"EXIT_PENDING_NO_QUOTE")
        close.assert_not_called()

    def test_generic_actions_are_not_replaced_by_fi_bridge(self):
        with self.store.connect() as db:
            db.execute("UPDATE paper_positions SET asset_class='ACCIONES'")
            p=dict(db.execute("SELECT * FROM paper_positions WHERE paper_id='PAPER-FI-1'").fetchone())
        q=FakeQuote()
        q.asset_class="ACCIONES"
        calls=[]

        def close(position,quote,reason,*,as_of):
            calls.append((position["paper_id"],reason,as_of))
            with self.store.connect() as db:
                db.execute("UPDATE paper_positions SET status='CLOSED',closed_at=? WHERE paper_id=?",
                           (as_of,position["paper_id"]))
        broker=SimpleNamespace(store=self.store,participation=Decimal("1"),quote_max_age_seconds=300)
        supervisor=PositionExitSupervisor(broker,clock_fn=lambda:AT,close_fn=close)
        verdict=supervisor.supervise(p,q,AT)
        self.assertEqual(verdict.state,"CLOSED")
        self.assertEqual(calls,[("PAPER-FI-1","EOD_PAPER",AT)])


if __name__ == "__main__":
    unittest.main()
