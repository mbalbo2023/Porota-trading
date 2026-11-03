"""A228 deterministic Scalping RCA/OOS/PAPER clock evidence, no provider calls.

Historical A198 HOLD totals have no raw paired PPI samples. Every positive
volume claim below is synthetic and every OOS split is a diagnostic with no
model freeze or entry authority. A real PAPER session gate is never patched.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import be_paper_engine as engine
import bu_instrument_catalog as catalog
import bv_paper_runtime as runtime
import cf_intraday_scalping as scalping
from bl_candle_engine import Bar, CandleArchive, Series
from bq_exit_policy import PaperSessionPolicy
from br_backtest_gate import DataRequirements, audit_dataset
from rc6_scalping_evidence_metrics import session_rca_snapshot, synthetic_purged_replay
from scripts.rc6_source_cycle_dispatch import phase_status
from tests.test_rc6_a222_spot_scalping_guarded_release import AT, SUMMARY, _points, _quote, _record


ART = ZoneInfo("America/Argentina/Buenos_Aires")


def replay_sample(paper_id, opened_at, closed_at, **changes):
    return {
        "paper_id": paper_id, "strategy_version": "SCALPING_SYNTHETIC_FROZEN_HYPOTHESIS_V1",
        "currency": "ARS", "features_available_at": opened_at,
        "opened_at": opened_at, "closed_at": closed_at,
        "label_available_at": closed_at, "label_source": "SYNTHETIC_TEST_ONLY",
        **changes,
    }


class A228ScalpingReplayTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="rc6-a228-scalping-")
        self.addCleanup(directory.cleanup)
        self.store = engine.PaperStore(str(Path(directory.name) / "paper.db"))
        catalog.init_schema(self.store)
        scalping.init_schema(self.store)

    @staticmethod
    def seed_two_reads(store, record, at):
        earlier = at - timedelta(minutes=1)
        scalping.persist_payload(store, record, _points(15, at=earlier),
                                 received_at=earlier.isoformat())
        return scalping.persist_payload(store, record, _points(16, at=at),
                                        received_at=at.isoformat())

    def test_paper_sqlite_metrics_are_causal_and_never_the_2582_provider_funnel(self):
        historical = json.loads(SUMMARY.read_text(encoding="utf-8"))
        self.assertEqual(historical["total_hold"], 2582)
        empty = session_rca_snapshot(self.store, as_of=AT)
        self.assertEqual(empty["candidate_evaluations_counted"], 0)
        self.assertEqual(empty["historical_2582_hold_comparability"],
                         "NOT_COMPARABLE_WITH_A198_AGGREGATE")
        self.assertEqual(empty["ppi_volume_semantics"], "NO_VERIFICADO_FROM_LOCAL_COUNTERS")
        self.assertEqual(empty["ppi_documented_response_fields"], ["date", "price", "volume"])
        self.assertEqual((empty["ppi_volume_unit"], empty["ppi_volume_accumulation"],
                          empty["ppi_volume_reset_rule"]),
                         ("NO_VERIFICADO", "NO_VERIFICADO", "NO_VERIFICADO"))
        self.assertFalse(empty["promotion_allowed"])

        record = _record()
        self.assertEqual(self.seed_two_reads(self.store, record, AT)["state"],
                         "CONFIRMED_INTERVAL_VOLUME")
        self.store.add_quote(_quote())
        self.assertEqual(scalping.evaluate_candidate(self.store, record, at=AT), "BUY_CANDIDATE")
        future = AT + timedelta(seconds=1)
        self.assertEqual(scalping.persist_payload(self.store, record, [],
                         received_at=future.isoformat())["state"], "EMPTY_INTRADAY_PAYLOAD")
        self.assertEqual(scalping.evaluate_candidate(self.store, record, at=future), "HOLD")

        # Exact cutoff must include the first microsecond-stamped decision,
        # while later changes to identity state cannot be rewound as history.
        prior = session_rca_snapshot(self.store, as_of=AT)
        self.assertEqual(prior["candidate_actions"], {"BUY_CANDIDATE": 1})
        self.assertEqual(prior["latest_identity_states"], {})
        current = session_rca_snapshot(self.store, as_of=future)
        self.assertEqual(current["candidate_actions"], {"BUY_CANDIDATE": 1, "HOLD": 1})
        self.assertEqual(current["candidate_hold_reasons"], {"EMPTY_INTRADAY_PAYLOAD": 1})
        self.assertEqual(current["latest_identity_states"], {"EMPTY_INTRADAY_PAYLOAD": 1})
        self.assertEqual(current["real_orders_sent"], 0)
        with patch("rc6_scalping_evidence_metrics.MAX_CANDIDATES", 1):
            bounded = session_rca_snapshot(self.store, as_of=future)
        self.assertEqual(bounded["candidate_evaluations_counted"], 1)
        self.assertFalse(bounded["candidate_evaluations_complete"])
        with patch("rc6_scalping_evidence_metrics.MAX_IDENTITIES", 0):
            with self.assertRaisesRegex(ValueError, "SCALPING_RCA_IDENTITY_CAPACITY_EXCEEDED"):
                session_rca_snapshot(self.store, as_of=future)
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0], 0)

    def test_missing_ppi_volume_contract_is_measured_but_never_authenticated(self):
        record = _record()
        record.pop("raw", None)
        self.assertEqual(self.seed_two_reads(self.store, record, AT)["state"],
                         "PPI_VOLUME_CONTRACT_UNVERIFIED")
        self.store.add_quote(_quote())
        self.assertEqual(scalping.evaluate_candidate(self.store, record, at=AT), "HOLD")
        report = session_rca_snapshot(self.store, as_of=AT)
        self.assertEqual(report["latest_identity_states"], {"PPI_VOLUME_CONTRACT_UNVERIFIED": 1})
        self.assertEqual(report["candidate_hold_reasons"], {"PPI_VOLUME_CONTRACT_UNVERIFIED": 1})
        self.assertEqual(report["provider_payload_pairs"], "NO_VERIFICADO")
        self.assertEqual(report["oos_edge"], "OOS_NO_VERIFICADO")

    def test_closed_session_replay_still_evaluates_hold_and_never_calls_broker(self):
        outside = (
            datetime.fromisoformat("2026-10-05T17:31:00-03:00"),
            datetime.fromisoformat("2026-10-31T10:46:00-03:00"),  # Saturday
            datetime.fromisoformat("2026-11-09T10:46:00-03:00"),  # audited BYMA holiday
        )
        for instant in outside:
            with self.subTest(instant=instant):
                # An isolated PAPER SQLite file for each synthetic market day.
                # Replay is allowed to run on a Saturday; PAPER opening is not.
                with tempfile.TemporaryDirectory(prefix="rc6-a228-closed-") as folder:
                    store = engine.PaperStore(str(Path(folder) / "paper.db"))
                    catalog.init_schema(store)
                    scalping.init_schema(store)
                    record = _record()
                    self.assertEqual(self.seed_two_reads(store, record, instant)["state"],
                                     "CONFIRMED_INTERVAL_VOLUME")
                    store.add_quote(_quote(at=instant))
                    self.assertFalse(scalping._market_open(instant))
                    self.assertEqual(scalping.evaluate_candidate(store, record, at=instant), "HOLD")
                    with store.connect() as connection:
                        self.assertEqual(connection.execute(
                            "SELECT reason FROM scalping_candidates ORDER BY id DESC LIMIT 1"
                        ).fetchone()[0], "OUTSIDE_SCALPING_PAPER_SESSION")
                    with patch.dict(os.environ, {"PAPER_SCALPING_MODE": "ACTIVE_PAPER"}), \
                         patch.object(runtime, "broker_from_environment",
                                      side_effect=AssertionError("NO_BROKER_OUTSIDE_SESSION")):
                        self.assertEqual(scalping.promote_paper_candidate(store, record,
                                         at=instant), "OUTSIDE_SCALPING_PAPER_SESSION")
                    with store.connect() as connection:
                        self.assertEqual(connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0], 0)
                        self.assertEqual(connection.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0], 0)

    def test_historical_market_open_replay_runs_24x7_but_t1_late_never_opens_paper(self):
        # This retrospective call works irrespective of the actual wall clock.
        record = _record()
        self.assertEqual(self.seed_two_reads(self.store, record, AT)["state"],
                         "CONFIRMED_INTERVAL_VOLUME")
        self.store.add_quote(_quote())
        self.assertEqual(scalping.evaluate_candidate(self.store, record, at=AT), "BUY_CANDIDATE")
        self.assertTrue(scalping._market_open(AT))
        for h, m, expected_intraday, expected_late in (
            (17, 29, True, True), (17, 30, False, True),
            (17, 59, False, True), (18, 0, False, False),
        ):
            at = datetime(2026, 11, 2, h, m, tzinfo=ART)
            with self.subTest(at=at):
                self.assertEqual(phase_status(at, "intraday_ppt_senebi")[0], expected_intraday)
                self.assertEqual(phase_status(at, "late_source")[0], expected_late)
                self.assertFalse(scalping._market_open(at))
                self.assertEqual(PaperSessionPolicy().admission_error({
                    "asset_class": "ACCIONES", "market": "BYMA", "settlement": "A-24HS"
                }, at), "OUTSIDE_PAPER_EXECUTION_WINDOW")

    def test_foreign_venue_and_unverified_etf_fxi_are_never_scanner_candidates(self):
        cases = [
            ("ALUA", "ACCIONES", "BYMA", "ARS", "READY_PAPER_SPOT"),
            ("AAPL", "CEDEARS", "BYMA", "USD_MEP", "READY_PAPER_SPOT"),
            ("SPY", "ETFS", "BYMA", "ARS", "NEEDS_EXACT_SOURCE_CONTRACT"),
            ("SPY_US", "ETFS", "NASDAQ", "USD", "READY_PAPER_SPOT"),
            ("MSFT", "ACCIONES_USA", "NYSE", "USD", "READY_PAPER_SPOT"),
            ("SPX", "INDICES", "BYMA", "ARS", "READY_PAPER_SPOT"),
        ]
        with self.store.connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS candidate_identity_v2(
              ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,
              settlement TEXT,can_simulate INTEGER,status TEXT,detail TEXT,
              checked_at TEXT)""")
            for symbol, family, market, currency, capability in cases:
                row = {"ticker":symbol, "instrument_type":family, "market":market,
                    "currency":currency, "settlement":"A-24HS",
                    "settlement_source":"PPI_FIELD", "description":"A228 synthetic",
                    "last_seen_at":AT.isoformat(), "run_id":"A228_SYNTHETIC",
                    "status":"AVAILABLE", "capability":capability,
                    "raw":{"_discovery_source":"PPI_PRIMARY"}}
                catalog.persist(connection, row)
                ready = capability == "READY_PAPER_SPOT"
                connection.execute("INSERT INTO candidate_identity_v2 VALUES(?,?,?,?,?,?,?,?,?)",
                    (symbol, family, market, currency, "A-24HS", int(ready),
                     "AVAILABLE" if ready else "PAUSED_EXPLICIT",
                     "SYNTHETIC_ONLY", AT.isoformat()))
        selected, _, count = scalping.select_batch(self.store, limit=8)
        self.assertEqual(count, 2)
        identities = {scalping._identity(row) for row in selected}
        self.assertEqual(identities, {
            ("ALUA", "ACCIONES", "BYMA", "ARS", "A-24HS"),
            ("AAPL", "CEDEARS", "BYMA", "USD_MEP", "A-24HS"),
        })
        # Even an adversarial approved SHADOW selection cannot reinsert an
        # unsupported market or turn INDEX benchmark/USA aliases into entries.
        forced = [scalping._identity(row) for row in selected] + [
            ("SPY_US", "ETFS", "NASDAQ", "USD", "A-24HS"),
            ("MSFT", "ACCIONES_USA", "NYSE", "USD", "A-24HS"),
            ("SPX", "INDICES", "BYMA", "ARS", "A-24HS"),
        ]
        controller = SimpleNamespace(
            state=lambda _at: {"status": "APPROVED_DYNAMIC"},
            selection=lambda *_args, **_kwargs: {"dynamic": True, "selected": forced})
        dynamic, _, _, detail = scalping.select_runtime_batch(
            self.store, limit=8, at=AT, controller=controller)
        self.assertTrue(detail["dynamic"])
        self.assertEqual({scalping._identity(row) for row in dynamic}, identities)

    def test_purged_synthetic_oos_split_is_reproducible_not_authority(self):
        samples = [
            replay_sample("train", "2026-10-05T11:00:00-03:00", "2026-10-05T11:20:00-03:00"),
            replay_sample("late-label", "2026-10-05T11:01:00-03:00", "2026-10-05T11:21:00-03:00",
                label_available_at="2026-10-05T15:01:00-03:00"),
            replay_sample("overlap", "2026-10-05T12:00:00-03:00", "2026-10-06T10:45:00-03:00"),
            replay_sample("test", "2026-10-06T11:00:00-03:00", "2026-10-06T11:20:00-03:00"),
            replay_sample("future-label", "2026-10-06T11:01:00-03:00", "2026-10-06T11:22:00-03:00",
                label_available_at="2026-10-09T11:22:00-03:00"),
        ]
        window = {"training_end": "2026-10-05T15:00:00-03:00",
                  "test_start": "2026-10-06T10:30:00-03:00",
                  "test_end": "2026-10-06T17:00:00-03:00",
                  "as_of": "2026-10-07T17:00:00-03:00", "embargo_seconds": 3600}
        result = synthetic_purged_replay(samples, **window)
        self.assertEqual(result, synthetic_purged_replay(list(reversed(samples)), **window))
        self.assertEqual(result["train_ids"], ["train"])
        self.assertEqual(result["test_ids"], ["test"])
        self.assertEqual(result["purged_ids"], ["future-label", "late-label", "overlap"])
        self.assertFalse(result["promotion_allowed"])
        self.assertFalse(result["frozen_model_attested"])
        self.assertIn("OOS_NO_VERIFICADO", result["strategy_validation"])
        forged = [{**samples[0], "label_source": "PPI_VERIFIED_LIVE"}]
        with self.assertRaisesRegex(ValueError, "SCALPING_SYNTHETIC_LABEL_SOURCE_REQUIRED"):
            synthetic_purged_replay(forged, **window)
        with self.assertRaisesRegex(ValueError, "Feature futura"):
            synthetic_purged_replay([{**samples[0],
                "features_available_at": "2026-10-05T12:00:00-03:00"}], **window)

    def test_revised_synthetic_candles_keep_known_at_and_never_become_ohlcv_oos(self):
        archive = CandleArchive(self.store)
        series = Series(symbol="ALUA", asset_class="ACCIONES", market="BYMA", currency="ARS",
            settlement="A-24HS", resolution="1m", source="SYNTHETIC_TEST_FIXTURE",
            adjustment="RAW", adjustment_basis="SYNTHETIC_FIXTURE_ONLY",
            price_kind="TRADE_SAMPLES", volume_kind="UNKNOWN", cash_multiplier="UNKNOWN")
        first_known, correction_known = AT + timedelta(minutes=1), AT + timedelta(minutes=3)
        archive.put(series, Bar(AT.isoformat(), Decimal("100"), Decimal("100"), Decimal("100"),
                    Decimal("100"), samples=1, quality="SAMPLED", synthetic=True),
                    known_at=first_known)
        archive.put(series, Bar(AT.isoformat(), Decimal("101"), Decimal("101"), Decimal("101"),
                    Decimal("101"), samples=1, quality="SAMPLED", synthetic=True),
                    known_at=correction_known)
        before = archive.read(series, as_of=first_known, qualities=None, include_synthetic=True)
        after = archive.read(series, as_of=correction_known, qualities=None, include_synthetic=True)
        self.assertEqual([row["close"] for row in before], ["100"])
        self.assertEqual([row["close"] for row in after], ["101"])
        self.assertNotEqual(before[0]["known_at"], after[0]["known_at"])
        self.assertEqual(archive.read(series, as_of=correction_known), [])
        audited = audit_dataset(archive, series, DataRequirements(1, 1, "QUANTITY"),
            start=AT, end=AT+timedelta(minutes=1), as_of=correction_known)
        self.assertTrue({"SAMPLES_ARE_NOT_COMPLETE_OHLC", "VOLUME_UNIT_MISMATCH",
                         "MISSING_VOLUME", "SESSION_GRID_NOT_VERIFIED"} <= set(audited["issues"]))
        self.assertFalse(audited["data_checks_passed"])
        self.assertFalse(audited["promotion_allowed"])

    def test_broken_paper_zero_evidence_is_not_eligible_for_rca_metrics(self):
        with self.store.connect() as connection:
            connection.execute("UPDATE observer_state SET real_orders_sent=1 WHERE id=1")
        with self.assertRaisesRegex(ValueError, "PAPER_ZERO_REQUIRED_FOR_SCALPING_RCA"):
            session_rca_snapshot(self.store, as_of=AT)


if __name__ == "__main__":
    unittest.main()
