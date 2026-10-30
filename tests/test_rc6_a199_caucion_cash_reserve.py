"""A199: no second reserve for FCI/futures cash already booked in PAPER.

These are isolated SQLite fixtures, not broker quotations or simulated fills.
"""
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sqlite3
import unittest
from zoneinfo import ZoneInfo

from be_paper_engine import PaperBroker, PaperStore
from di_caucion_cash_sweep_runtime_hf6 import (
    obligation_snapshot_from_ledger, required_reserve,
)
from rc6_paper_family_lifecycle import fund_cash_effect, future_cash_effect


TZ = ZoneInfo("America/Argentina/Buenos_Aires")
AT = datetime(2026, 10, 30, 16, 45, tzinfo=TZ)
DEADLINE = AT + timedelta(hours=24)


class FamilyObligationReserveTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = PaperStore(str(Path(directory.name) / "paper.db"))

    def record(self, *, family="FCI", aggregate="-25000", events=(),
               metadata=None):
        detail = metadata if metadata is not None else {
            "settlement_at": (AT + timedelta(hours=1)).isoformat()}
        rendered = json.dumps(detail)
        state = events[-1][0] if events else "SUBSCRIBE_REQUESTED"
        updated_at = events[-1][2] if events else AT
        with self.store.connect() as db:
            db.execute("""INSERT INTO paper_family_lifecycle VALUES(
              'a199-1',?, 'PAPER-INSTRUMENT', 'ARS', ?, ?, ?, ?)""",
              (family, state, updated_at.isoformat(), aggregate, rendered))
            previous = None
            for index, (state, amount, moment, event_family) in enumerate(events):
                db.execute("""INSERT INTO paper_family_lifecycle_events VALUES(
                  ?, 'a199-1', ?, ?, ?, ?, ?, ?)""",
                  (f"a199-{index}", event_family or family, previous, state,
                   str(amount), moment.isoformat(), rendered))
                previous = state

    def snapshot(self):
        return obligation_snapshot_from_ledger(
            self.store, observed_at=AT, liquidity_deadline=DEADLINE)

    def test_fci_subscribe_cash_event_is_not_reserved_twice(self):
        self.record(events=[("SUBSCRIBE_REQUESTED", "-25000", AT, None)])
        self.assertEqual(fund_cash_effect(self.store, "ARS", AT), Decimal("-25000"))
        self.assertEqual(PaperBroker(self.store, initial_cash="100000")._cash(
            AT, "ARS", for_execution=True), Decimal("75000"))
        evidence = self.snapshot()
        self.assertTrue(evidence.complete)
        self.assertEqual(required_reserve(evidence, currency="ARS",
                                          liquidity_deadline=DEADLINE), Decimal("0"))

    def test_future_margin_already_debited_is_not_reserved_twice(self):
        self.record(family="FUTUROS", aggregate="-5000",
                    events=[("OPEN", "0", AT, None),
                            ("MARGIN_RESERVED", "-5000", AT, None)])
        self.assertEqual(future_cash_effect(self.store, "ARS", AT), Decimal("-5000"))
        evidence = self.snapshot()
        self.assertTrue(evidence.complete)
        self.assertEqual(evidence.obligations, ())

    def test_legacy_aggregate_without_events_remains_fully_reserved(self):
        self.record()
        evidence = self.snapshot()
        self.assertTrue(evidence.complete)
        self.assertEqual(required_reserve(evidence, currency="ARS",
                                          liquidity_deadline=DEADLINE), Decimal("25000"))

    def test_unknown_fci_non_cash_delta_retains_conservative_reserve(self):
        self.record(aggregate="-30000", events=[
            ("SUBSCRIBE_REQUESTED", "-25000", AT, None),
            ("PENDING", "0", AT, None),
            ("NAV_APPLIED", "-5000", AT, None),
        ])
        evidence = self.snapshot()
        self.assertTrue(evidence.complete)
        self.assertEqual(required_reserve(evidence, currency="ARS",
                                          liquidity_deadline=DEADLINE), Decimal("5000"))

    def test_event_aggregate_mismatch_never_releases_reserve(self):
        self.record(events=[("SUBSCRIBE_REQUESTED", "-5000", AT, None)])
        self.assertFalse(self.snapshot().complete)

    def test_future_dated_event_never_releases_reserve(self):
        self.record(events=[("SUBSCRIBE_REQUESTED", "-25000",
                             AT + timedelta(seconds=1), None)])
        self.assertFalse(self.snapshot().complete)

    def test_reversed_event_time_chain_never_releases_reserve(self):
        self.record(events=[
            ("SUBSCRIBE_REQUESTED", "-25000", AT, None),
            ("PENDING", "0", AT - timedelta(seconds=1), None),
        ])
        self.assertFalse(self.snapshot().complete)

    def test_cross_family_event_never_releases_reserve(self):
        self.record(events=[("SUBSCRIBE_REQUESTED", "-25000", AT, "FUTUROS")])
        self.assertFalse(self.snapshot().complete)

    def test_mixed_correct_and_cross_family_events_never_release_cash(self):
        self.record(events=[
            ("SUBSCRIBE_REQUESTED", "-25000", AT, None),
            ("PENDING", "0", AT, "FUTUROS"),
        ])
        self.assertFalse(self.snapshot().complete)

    def test_corrupted_last_receipt_and_lifecycle_row_fail_closed(self):
        self.record(events=[("SUBSCRIBE_REQUESTED", "-25000", AT, None)])
        with self.store.connect() as db:
            db.execute("UPDATE paper_family_lifecycle_events SET detail_json='{}'")
        self.assertFalse(self.snapshot().complete)
        with self.store.connect() as db:
            db.execute("UPDATE paper_family_lifecycle SET state='REDEEMED'")
        self.assertFalse(self.snapshot().complete)

    def test_duplicate_event_id_rejected_by_sqlite_and_snapshot_unchanged(self):
        self.record(events=[("SUBSCRIBE_REQUESTED", "-25000", AT, None)])
        before = self.snapshot()
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.connect() as db:
                db.execute("""INSERT INTO paper_family_lifecycle_events VALUES(
                    'a199-0','a199-1','FCI',NULL,'SUBSCRIBE_REQUESTED',
                    '-25000',?,'{}')""", (AT.isoformat(),))
        self.assertEqual(self.snapshot(), before)

    def test_blank_event_id_never_unlocks_budget(self):
        self.record(events=[("SUBSCRIBE_REQUESTED", "-25000", AT, None)])
        with self.store.connect() as db:
            db.execute("UPDATE paper_family_lifecycle_events SET event_id='' ")
        self.assertFalse(self.snapshot().complete)

    def test_missing_event_schema_remains_incomplete(self):
        self.record(events=[("SUBSCRIBE_REQUESTED", "-25000", AT, None)])
        with self.store.connect() as db:
            db.execute("DROP TABLE paper_family_lifecycle_events")
        self.assertFalse(self.snapshot().complete)

    def test_positive_cash_family_rows_do_not_skip_future_event_validation(self):
        self.record(family="FUTUROS", aggregate="5000",
                    events=[("OPEN", "5000", AT + timedelta(seconds=1), None)])
        self.assertFalse(self.snapshot().complete)

    def test_positive_row_with_mixed_cross_family_events_fails_closed(self):
        self.record(family="FUTUROS", aggregate="5000", events=[
            ("OPEN", "5000", AT, None),
            ("MARGIN_RESERVED", "0", AT, "FCI"),
        ])
        self.assertFalse(self.snapshot().complete)

    def test_zero_balance_future_still_checks_event_provenance(self):
        self.record(family="FUTUROS", aggregate="0",
                    events=[("OPEN", "0", AT, None)])
        self.assertTrue(self.snapshot().complete)
        self.assertEqual(self.snapshot().obligations, ())
        with self.store.connect() as db:
            db.execute("UPDATE paper_family_lifecycle_events SET event_id='' ")
        self.assertFalse(self.snapshot().complete)

    def test_unbacked_legacy_positive_cash_credit_fails_closed(self):
        self.record(family="FCI", aggregate="5000", events=[])
        self.assertFalse(self.snapshot().complete)

    def test_invalid_currency_cannot_bypass_validation_when_net_reserve_zero(self):
        self.record(events=[("SUBSCRIBE_REQUESTED", "-25000", AT, None)])
        with self.store.connect() as db:
            db.execute("UPDATE paper_family_lifecycle SET currency='INVALID_CURRENCY'")
        self.assertFalse(self.snapshot().complete)

    def test_snapshot_is_stable_and_read_only(self):
        self.record(events=[("SUBSCRIBE_REQUESTED", "-25000", AT, None)])
        with self.store.connect() as db:
            before = tuple(db.execute("SELECT * FROM paper_family_lifecycle"))
            receipts_before = tuple(db.execute("SELECT * FROM paper_family_lifecycle_events"))
        first = self.snapshot()
        self.assertEqual(self.snapshot(), first)
        with self.store.connect() as db:
            self.assertEqual(tuple(db.execute("SELECT * FROM paper_family_lifecycle")), before)
            self.assertEqual(tuple(db.execute("SELECT * FROM paper_family_lifecycle_events")), receipts_before)

    def test_invalid_fci_cash_direction_never_releases_reserve(self):
        self.record(aggregate="-20000", events=[
            ("SUBSCRIBE_REQUESTED", "5000", AT, None),
            ("SUBSCRIBE", "-25000", AT, None),
        ])
        self.assertFalse(self.snapshot().complete)

    def test_already_posted_cash_still_requires_valid_metadata(self):
        self.record(events=[("SUBSCRIBE_REQUESTED", "-25000", AT, None)],
                    metadata={"settlement_at": "unknown"})
        self.assertFalse(self.snapshot().complete)


if __name__ == "__main__":
    unittest.main()
