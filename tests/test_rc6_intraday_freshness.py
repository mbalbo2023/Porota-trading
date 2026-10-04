"""Offline entry regression guards: fresh books cannot revive old intraday data."""
import json
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import cf_intraday_scalping as scalping


class Store:
    def __init__(self, path):
        self.path = str(path)
        self.decisions = []
        self.gates = []
        self.events = []
        self.audit_http = Mock()

    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def record_decision(self, *args):
        self.decisions.append(args)
        return True

    def record_gates(self, *args, **kwargs):
        self.gates.append((args, kwargs))

    def event(self, *args):
        self.events.append(args)


class IntradayFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="rc6-freshness-")
        self.addCleanup(self.directory.cleanup)
        self.store = Store(Path(self.directory.name) / "paper.db")
        scalping.init_schema(self.store)
        with self.store.connect() as connection:
            connection.executescript("""
                CREATE TABLE market_snapshots(
                  id INTEGER PRIMARY KEY, symbol TEXT, asset_class TEXT,
                  settlement TEXT, currency TEXT, market TEXT, bid TEXT,
                  ask TEXT, book_at TEXT, last TEXT, bid_size TEXT,
                  ask_size TEXT, observed_at TEXT, metadata_source TEXT,
                  opening_block_reason TEXT, trade_at TEXT, last_kind TEXT);
                CREATE TABLE paper_positions(
                  id INTEGER PRIMARY KEY, status TEXT, features_json TEXT);
            """)
        self.record = dict(ticker="GGAL", instrument_type="ACCIONES", market="BYMA",
                           currency="ARS", settlement="A-24HS",
                           capability="READY_PAPER_SPOT", status="AVAILABLE")
        self.start = datetime.fromisoformat("2026-10-05T10:30:00-03:00")
        self.persist(15, "2026-10-05T10:45:00-03:00")
        confirmed = self.persist(16, "2026-10-05T10:46:00-03:00")
        self.assertEqual(confirmed["state"], "CONFIRMED_INTERVAL_VOLUME")
        self.quote("2026-10-05T10:46:00-03:00")
        self.mode = patch.dict(os.environ, {"PAPER_SCALPING_MODE": "ACTIVE_PAPER"})
        self.mode.start()
        self.addCleanup(self.mode.stop)

    def payload(self, count):
        return [{"date": (self.start + timedelta(minutes=index)).isoformat(),
                 "price": str(100 + index / 4),
                 "volume": str(20 if index % 2 == 0 else 10)}
                for index in range(count)]

    def persist(self, count, at):
        points = scalping.normalize_payload(self.payload(count), received_at=at)
        return scalping.persist_payload(self.store, self.record, points, received_at=at)

    def quote(self, at, price="103.75"):
        with self.store.connect() as connection:
            connection.execute("DELETE FROM market_snapshots")
            connection.execute("""INSERT INTO market_snapshots
              VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              ("GGAL", "ACCIONES", "A-24HS", "ARS", "BYMA", price,
               str(float(price) + .05), at, price, "1000", "1000", at,
               "PPI_MARKETDATA", "", at, "TRADE"))

    def state(self):
        with self.store.connect() as connection:
            return dict(connection.execute("SELECT * FROM ppi_intraday_contract_state").fetchone())

    def candidate(self):
        with self.store.connect() as connection:
            return dict(connection.execute("SELECT * FROM scalping_candidates ORDER BY id DESC LIMIT 1").fetchone())

    def update_state(self, field, value):
        # Field names are test constants; values are always bound parameters.
        with self.store.connect() as connection:
            connection.execute(f"UPDATE ppi_intraday_contract_state SET {field}=?", (value,))

    def assert_hold(self, at, reason):
        self.assertEqual(scalping.evaluate_candidate(self.store, self.record, at=at), "HOLD")
        self.assertEqual(self.candidate()["reason"], reason)
        self.assertFalse(json.loads(self.candidate()["economics_json"])["passed"])

    def broker_modules(self):
        # A broker constructor is a visible side effect; rejected entries must
        # return before importing it or recording an opening decision.
        class Quote:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        broker = Mock()
        broker._open.return_value = (True, "OPENED_SIMULATED", "paper-1")
        factory = Mock(return_value=broker)
        engine_module = types.ModuleType("be_paper_engine")
        engine_module.Quote = Quote
        runtime_module = types.ModuleType("bv_paper_runtime")
        runtime_module.broker_from_environment = factory
        return {"be_paper_engine": engine_module, "bv_paper_runtime": runtime_module}, factory, broker

    def assert_promotion_blocked(self, at, reason):
        modules, factory, broker = self.broker_modules()
        with patch.dict(sys.modules, modules):
            self.assertEqual(scalping.promote_paper_candidate(self.store, self.record, at=at), reason)
        factory.assert_not_called()
        broker._open.assert_not_called()
        self.assertEqual(self.store.decisions, [])

    def test_empty_payload_revokes_confirmation_and_keeps_history(self):
        at = "2026-10-05T11:05:00-03:00"
        self.quote(at)
        result = scalping.persist_payload(self.store, self.record, [], received_at=at)
        self.assertEqual(result["state"], "EMPTY_INTRADAY_PAYLOAD")
        self.assertIsNone(self.state()["last_source_at"])
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ppi_intraday_points").fetchone()[0], 16)
        self.assert_hold(at, "EMPTY_INTRADAY_PAYLOAD")
        self.assert_promotion_blocked(at, "EMPTY_INTRADAY_PAYLOAD")

    def test_old_source_is_blocked_despite_fresh_response_and_book(self):
        at = "2026-10-05T11:05:00-03:00"
        # A repeated nonempty response renews checked_at, but cannot make the
        # last provider minute (10:45) current.
        self.persist(16, at)
        self.quote(at)
        self.assertEqual(self.state()["state"], "CONFIRMED_INTERVAL_VOLUME")
        self.assert_hold(at, "STALE_INTRADAY_SOURCE")
        self.assert_promotion_blocked(at, "STALE_INTRADAY_SOURCE")

    def test_source_and_observation_timestamps_fail_closed(self):
        at = "2026-10-05T10:46:00-03:00"
        original = self.state()
        cases = [
            ("last_source_at", None, "NO_CURRENT_INTRADAY_SOURCE"),
            ("last_source_at", "invalid", "INVALID_INTRADAY_TIMESTAMP"),
            ("last_source_at", "2026-10-05T10:45:00", "INVALID_INTRADAY_TIMESTAMP"),
            ("last_source_at", "2026-10-05T10:46:01-03:00", "INTRADAY_TIMESTAMP_IN_FUTURE"),
            ("checked_at", "invalid", "INVALID_INTRADAY_TIMESTAMP"),
            ("checked_at", "2026-10-05T10:46:01-03:00", "INTRADAY_TIMESTAMP_IN_FUTURE"),
            ("checked_at", "2026-10-05T10:44:59-03:00", "INTRADAY_TIMESTAMP_IN_FUTURE"),
            ("last_source_at", "2026-10-05T10:45:30-03:00", "INTRADAY_SOURCE_MISMATCH"),
        ]
        for field, value, reason in cases:
            self.update_state("last_source_at", original["last_source_at"])
            self.update_state("checked_at", original["checked_at"])
            self.update_state(field, value)
            # A distinct evaluation timestamp avoids INSERT OR IGNORE
            # hiding the reason from a prior subtest.
            with self.store.connect() as connection:
                connection.execute("DELETE FROM scalping_candidates")
            self.assert_hold(at, reason)
            self.assert_promotion_blocked(at, reason)

    def test_latest_source_future_within_normalizer_tolerance_still_blocks_entry(self):
        at = "2026-10-05T10:46:00-03:00"
        payload = self.payload(16) + [{"date": "2026-10-05T10:46:04-03:00", "price": "104", "volume": "20"}]
        points = scalping.normalize_payload(payload, received_at=at)
        scalping.persist_payload(self.store, self.record, points, received_at=at)
        self.assert_hold(at, "INTRADAY_TIMESTAMP_IN_FUTURE")
        self.assert_promotion_blocked(at, "INTRADAY_TIMESTAMP_IN_FUTURE")

    def test_stale_checked_at_and_invalid_provider_dates_are_blocked(self):
        self.update_state("checked_at", "2026-10-05T10:44:00-03:00")
        # Source newer than observation is inconsistent even when both are
        # parseable; this must not be usable opening evidence.
        self.assert_hold("2026-10-05T10:46:00-03:00", "INTRADAY_TIMESTAMP_IN_FUTURE")
        for timestamp in ("invalid", "2026-10-05T10:45:00", "2026-10-05T10:46:06-03:00"):
            with self.assertRaises(ValueError):
                scalping.normalize_payload([{"date": timestamp, "price": "104", "volume": "20"}],
                                           received_at="2026-10-05T10:46:00-03:00")

    def test_freshness_limit_is_two_minutes_and_inclusive(self):
        self.assertEqual(scalping.INTRADAY_SOURCE_MAX_AGE_SECONDS, 120)
        at = "2026-10-05T10:47:00-03:00"
        self.quote(at)
        self.assertEqual(scalping.evaluate_candidate(self.store, self.record, at=at), "BUY_CANDIDATE")
        at = "2026-10-05T10:47:00.000001-03:00"
        self.quote(at)
        self.assert_hold(at, "STALE_INTRADAY_SOURCE")

    def test_fresh_payload_recovers_after_empty_without_losing_history(self):
        empty_at = "2026-10-05T11:05:00-03:00"
        scalping.persist_payload(self.store, self.record, [], received_at=empty_at)
        at = "2026-10-05T11:07:00-03:00"
        recovered = self.persist(37, at)
        self.assertEqual(recovered["state"], "CONFIRMED_INTERVAL_VOLUME")
        self.quote(at, "109")
        self.assertEqual(scalping.evaluate_candidate(self.store, self.record, at=at), "BUY_CANDIDATE")
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ppi_intraday_points").fetchone()[0], 37)
        modules, factory, broker = self.broker_modules()
        with patch.dict(sys.modules, modules):
            self.assertEqual(scalping.promote_paper_candidate(self.store, self.record, at=at), "OPENED_SIMULATED")
        factory.assert_called_once()
        broker._open.assert_called_once()
        self.assertEqual(len(self.store.decisions), 1)

    def test_delayed_promotion_rechecks_source_before_constructing_broker(self):
        self.assertEqual(scalping.evaluate_candidate(self.store, self.record, at="2026-10-05T10:46:00-03:00"),
                         "BUY_CANDIDATE")
        at = "2026-10-05T10:49:00-03:00"
        self.quote(at)
        self.assert_promotion_blocked(at, "STALE_INTRADAY_SOURCE")

    def test_refreshing_source_does_not_revive_old_candidate(self):
        self.assertEqual(scalping.evaluate_candidate(self.store, self.record, at="2026-10-05T10:46:00-03:00"),
                         "BUY_CANDIDATE")
        at = "2026-10-05T10:50:00-03:00"
        self.persist(20, at)
        self.quote(at)
        self.assert_promotion_blocked(at, "STALE_SCALPING_CANDIDATE")

    def test_delayed_promotion_preserves_existing_book_gate(self):
        self.assertEqual(scalping.evaluate_candidate(self.store, self.record, at="2026-10-05T10:46:00-03:00"),
                         "BUY_CANDIDATE")
        self.quote("2026-10-05T10:44:00-03:00")
        self.assert_promotion_blocked("2026-10-05T10:47:00-03:00", "STALE_BOOK")

    def test_invalid_or_future_candidate_and_book_cannot_reach_broker(self):
        at = "2026-10-05T10:46:00-03:00"
        self.assertEqual(scalping.evaluate_candidate(self.store, self.record, at=at), "BUY_CANDIDATE")
        cases = [
            ("candidate", "invalid", "INVALID_SCALPING_CANDIDATE_TIMESTAMP"),
            ("candidate", "2026-10-05T10:46:01-03:00", "STALE_SCALPING_CANDIDATE"),
            ("book", "invalid", "INVALID_BOOK_TIMESTAMP"),
            ("book", "2026-10-05T10:46:01-03:00", "STALE_BOOK"),
        ]
        for kind, timestamp, reason in cases:
            self.quote(at)
            with self.store.connect() as connection:
                connection.execute("UPDATE scalping_candidates SET evaluated_at=?", (scalping._stamp(at),))
                if kind == "candidate":
                    connection.execute("UPDATE scalping_candidates SET evaluated_at=?", (timestamp,))
                else:
                    connection.execute("UPDATE market_snapshots SET book_at=?", (timestamp,))
            self.assert_promotion_blocked(at, reason)

    def test_empty_response_does_not_clear_a_closed_minute_rejection(self):
        self.update_state("changed_closed_points", 1)
        self.update_state("state", "REJECTED_MUTABLE_CLOSED_POINTS")
        at = "2026-10-05T10:47:00-03:00"
        rejected = scalping.persist_payload(self.store, self.record, [], received_at=at)
        self.assertEqual(rejected["state"], "REJECTED_MUTABLE_CLOSED_POINTS")
        rejected = self.persist(18, "2026-10-05T10:48:00-03:00")
        self.assertEqual(rejected["state"], "REJECTED_MUTABLE_CLOSED_POINTS")
        self.assertEqual(self.state()["changed_closed_points"], 1)

    def test_entry_rejection_preserves_open_and_closed_positions_for_exit_owner(self):
        # Source failures are entry guards only; existing position/exit state
        # belongs to the engine and must remain intact for its independent loop.
        with self.store.connect() as connection:
            connection.execute("INSERT INTO paper_positions VALUES(1,'OPEN','{}')")
            connection.execute("INSERT INTO paper_positions VALUES(2,'CLOSED','{}')")
            before = [tuple(row) for row in connection.execute("SELECT * FROM paper_positions ORDER BY id")]
        at = "2026-10-05T11:05:00-03:00"
        scalping.persist_payload(self.store, self.record, [], received_at=at)
        self.assert_hold(at, "EMPTY_INTRADAY_PAYLOAD")
        self.assert_promotion_blocked(at, "EMPTY_INTRADAY_PAYLOAD")
        with self.store.connect() as connection:
            after = [tuple(row) for row in connection.execute("SELECT * FROM paper_positions ORDER BY id")]
        self.assertEqual(after, before)
        self.assertEqual(self.store.gates, [])

    def test_worker_uses_current_clock_again_at_promotion(self):
        class Stop:
            stopped = False

            def is_set(self):
                return self.stopped

            def wait(self, _seconds):
                self.stopped = True

        reader = Mock()
        reader.metrics = {"http_blocked": 0}
        reader.intraday.return_value = []
        guard = types.ModuleType("bd_ppi_readonly_guard")
        guard.ProductionMarketReader = Mock(return_value=reader)
        guard.retry_read = lambda call, **_kwargs: call()
        guard.session_invalid = lambda _exc: False
        guard.classify_read_error = lambda exc: type(exc).__name__
        guard.instrument_not_found = lambda _exc: False
        observer = types.ModuleType("bf_production_paper_observer")
        observer._secret = lambda: ()
        at = "2026-10-05T10:46:00-03:00"
        later = "2026-10-05T10:49:00-03:00"
        clock = Mock(side_effect=[at, at, later, later, later])
        with patch.dict(sys.modules, {guard.__name__: guard, observer.__name__: observer}), \
             patch.object(scalping, "_market_open", return_value=True), \
             patch.object(scalping, "select_batch", return_value=([self.record], 0, 1)), \
             patch.object(scalping, "persist_payload", return_value={"inserted": 0, "state": "CONFIRMED_INTERVAL_VOLUME"}), \
             patch.object(scalping, "evaluate_candidate", return_value="BUY_CANDIDATE") as evaluate, \
             patch.object(scalping, "promote_paper_candidate", return_value="STALE_INTRADAY_SOURCE") as promote, \
             patch.object(scalping, "_heartbeat"):
            scalping.run_worker(self.store, Stop(), clock_fn=clock)
        self.assertEqual(evaluate.call_args.kwargs["at"], scalping._stamp(at))
        self.assertEqual(promote.call_args.kwargs["at"], scalping._stamp(later))


if __name__ == "__main__":
    unittest.main()
