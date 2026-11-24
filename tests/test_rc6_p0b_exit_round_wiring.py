"""P0-B: factual EXIT cycles reach the existing shared-budget observer.

Runs the actual PAPER reader loop against an in-memory fake transport;
no provider GET, quota authority, trading sidecar or PPI Watch is used.
"""

from contextlib import ExitStack
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

import bv_paper_runtime as runtime


class StopAfterFirstRound:
    def __init__(self, after_waits=1):
        self.stopped = False
        self.waits = 0
        self.after_waits = after_waits

    def is_set(self):
        return self.stopped

    def wait(self, _seconds):
        self.waits += 1
        self.stopped = self.waits >= self.after_waits


class FakeExitReader:
    def __init__(self, outcome="COMPLETE"):
        self.outcome = outcome
        self.observations = []
        self.discards = 0
        self.closed = False
        self.login_calls = 0
        self.provider_gets = 0

    def login_once(self):
        self.login_calls += 1
        raise AssertionError("EXIT_CHILD_SECOND_PPI_LOGIN_FORBIDDEN")

    def observe_exit_round(self, **record):
        self.observations.append(record)
        return {"status": self.outcome, "lower_suspended": self.outcome != "COMPLETE"}

    def discard_exit_round_scope(self):
        self.discards += 1

    def close(self):
        self.closed = True


def run_one(collector, *, reader=None, clock=None, status_result=True, stop_after=1,
            exercise_beat=False):
    stop = StopAfterFirstRound(stop_after)
    reader = reader or FakeExitReader()
    statuses, events = [], []
    with tempfile.TemporaryDirectory(prefix="rc6-p0b-test-") as directory, ExitStack() as stack:
        store = SimpleNamespace(path=Path(directory) / "paper.sqlite",
                                audit_http=lambda *_: None,
                                event=lambda *args: events.append(args))
        # run_reader is genuine, while owner SDK and market phase are isolated.
        # No provider GET/login, second account reader or on-wire transport.
        guarded = ModuleType("bd_ppi_readonly_guard")
        guarded.session_invalid = lambda exc: False
        observer = ModuleType("bf_production_paper_observer")
        observer._market_phase = lambda: "OPEN"
        observer._secret = lambda: ("FAKE", "FAKE")
        observer._support_schema = lambda store: None
        gateway = ModuleType("rc6_ppi_gateway_reader_adapter")
        def fake_gateway_connect(database, *, consumer):
            assert database == store.path and consumer == "exit_reader"
            return reader  # consumer-side reader only; owner login is not retried
        gateway.connect_paper_gateway_reader = fake_gateway_connect
        gateway.gateway_reader_needs_reattach = lambda exc: False
        stack.enter_context(patch.dict(os.environ, {"POROTA_RUNTIME_SCHEMA_READY": "1"}))
        stack.enter_context(patch.dict(sys.modules, {
            "bd_ppi_readonly_guard": guarded,
            "bf_production_paper_observer": observer,
            "rc6_ppi_gateway_reader_adapter": gateway,
        }))
        def collect_with_heartbeat(*args, **kwargs):
            if exercise_beat:
                kwargs["beat"]()
            return collector(stop)
        stack.enter_context(patch.object(runtime, "collect_exit_books", collect_with_heartbeat))
        stack.enter_context(patch.object(runtime, "exit_reader_cadence", lambda *a: 5.0))
        stack.enter_context(patch.object(runtime, "_reader_status_write", lambda s, state, detail="": (
            statuses.append((state, detail)), status_result)[1]))
        if clock is not None:
            ticks = iter(clock)
            stack.enter_context(patch.object(runtime, "time", SimpleNamespace(monotonic=lambda: next(ticks))))

        runtime.run_reader(store, stop)
        sidecar_existed = (Path(directory) / "ppi-budget" / "global.sqlite").exists()
    return reader, statuses, events, sidecar_existed


class ExitRoundWiring(unittest.TestCase):
    def test_completed_cycle_publishes_elapsed_and_failures(self):
        reader, statuses, events, sidecar = run_one(lambda stop: 0)
        self.assertEqual(reader.login_calls, 0)  # child never authenticates to provider
        self.assertIn(("PPI_GATEWAY_ATTACHED", "consumer=exit_reader;no_sdk_login"), events)
        self.assertTrue(reader.closed)
        self.assertEqual(reader.provider_gets, 0)
        self.assertFalse(sidecar)
        self.assertEqual(len(reader.observations), 1)
        self.assertEqual(reader.observations[0]["failures"], 0)
        self.assertEqual(reader.observations[0]["deadline_seconds"], 5)
        self.assertGreaterEqual(reader.observations[0]["elapsed_seconds"], 0)
        self.assertEqual(statuses[-1][0], "STOPPED")

    def test_stop_mid_collection_discards_scope_without_false_complete(self):
        def interrupted(stop):
            stop.stopped = True
            return 0  # Existing collect_exit_books returns zero on early break.

        reader, _, _, _ = run_one(interrupted)
        self.assertEqual(reader.observations, [])
        self.assertGreaterEqual(reader.discards, 1)

    def test_book_failure_is_forwarded_not_dropped(self):
        reader, statuses, _, _ = run_one(lambda stop: 1,
                                         reader=FakeExitReader(outcome="DEGRADED"))
        self.assertEqual(len(reader.observations), 1)
        self.assertEqual(reader.observations[0]["failures"], 1)
        self.assertEqual(statuses[-2][0], "DEGRADED")

    def test_budget_observation_degraded_cannot_be_reported_as_ready(self):
        reader, statuses, _, _ = run_one(
            lambda stop: 0, reader=FakeExitReader(outcome="DEGRADED"))
        self.assertEqual(len(reader.observations), 1)
        self.assertEqual(statuses[-2][0], "DEGRADED")
        self.assertIn("budget_round=DEGRADED", statuses[-2][1])

    def test_no_ready_window_on_gateway_attach_or_an_unmeasured_round(self):
        _, statuses, _, _ = run_one(lambda stop: 0,
            reader=FakeExitReader(outcome="BASELINE_NOT_MEASURED"), exercise_beat=True)
        self.assertNotIn("READY", [state for state, _ in statuses])
        self.assertIn("VERIFYING", [state for state, _ in statuses])
        self.assertIn("budget_round=BASELINE_NOT_MEASURED", statuses[-2][1])

    def test_first_ready_is_only_published_after_complete_round(self):
        _, statuses, _, _ = run_one(lambda stop: 0, exercise_beat=True)
        self.assertEqual([state for state, _ in statuses].count("READY"), 1)
        self.assertIn("budget_round=COMPLETE", statuses[-2][1])

    def test_timeout_forwards_real_elapsed_not_synthetic_deadline(self):
        reader, statuses, _, _ = run_one(lambda stop: 0,
                                         reader=FakeExitReader(outcome="DEGRADED"),
                                         clock=[100.0, 101.0, 107.0])
        self.assertEqual(len(reader.observations), 1)
        self.assertEqual(reader.observations[0]["elapsed_seconds"], 6)
        self.assertEqual(reader.observations[0]["deadline_seconds"], 5)
        self.assertEqual(statuses[-2][0], "DEGRADED")

    def test_sqlite_contention_aborts_round_and_discards_scope(self):
        def contended(stop):
            raise sqlite3.OperationalError("database is locked")

        reader, statuses, _, _ = run_one(contended)
        self.assertEqual(reader.observations, [])
        self.assertGreaterEqual(reader.discards, 1)
        self.assertTrue(any(s == "DEGRADED" for s, _ in statuses))

    def test_no_quota_and_health_contention_do_not_create_sidecar(self):
        reader, _, _, sidecar = run_one(
            lambda stop: 0, reader=FakeExitReader(outcome="BASELINE_NOT_MEASURED"),
            status_result=False)
        self.assertEqual(len(reader.observations), 1)
        self.assertEqual(reader.provider_gets, 0)
        self.assertFalse(sidecar)

    def test_discard_failure_cannot_reuse_reader_for_second_round(self):
        class BrokenDiscardReader(FakeExitReader):
            def discard_exit_round_scope(self):
                raise RuntimeError("FROZEN_SCOPE_CANNOT_BE_CLEARED")

        reader = BrokenDiscardReader()
        with self.assertRaisesRegex(RuntimeError, "EXIT_READER_SCOPE_RESET_UNVERIFIED"):
            run_one(lambda stop: 0, reader=reader, stop_after=2)
        self.assertEqual(len(reader.observations), 1)
        self.assertTrue(reader.closed)

    def test_sqlite_busy_in_discard_is_not_treated_as_recoverable_book_contention(self):
        class BusyDiscardReader(FakeExitReader):
            def discard_exit_round_scope(self):
                raise sqlite3.OperationalError("database is locked")

        reader = BusyDiscardReader()
        with self.assertRaisesRegex(RuntimeError, "EXIT_READER_SCOPE_RESET_UNVERIFIED"):
            run_one(lambda stop: 0, reader=reader, stop_after=2)
        self.assertEqual(len(reader.observations), 1)
        self.assertTrue(reader.closed)


if __name__ == "__main__":
    unittest.main()
