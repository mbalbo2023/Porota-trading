"""A222 bounded PAPER-only regressions for the historical Scalping HOLD funnel.

The 29-Oct A198 counts are *historical aggregate evidence*, never synthetic
provider payloads or evidence of today's market. The synthetic volume contract
used by positive unit paths MUST NOT be treated as PPI/provider authorization.

This suite covers the exact former rejection classes without disabling the
production entry gates or converting an unknown provider field into volume.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import be_paper_engine as engine
import bu_instrument_catalog as catalog
import bv_paper_runtime as runtime
import cf_intraday_scalping as scalping
import rc6_paper_admission_gate as preopen
from bm_exit_supervisor import PositionExitSupervisor, init_schema as init_exit
from bq_exit_policy import PaperSessionPolicy
from rc6_dynamic_universe.routing import strategy_route
from rc6_signal_contracts import volume_contract
from tests.rc6_convergence_fixtures import with_synthetic_volume_contract


AT = datetime.fromisoformat("2026-10-05T10:46:00-03:00")
SUMMARY = Path(__file__).parent / "fixtures/rc6_a198_scalping_hold_summary_20261029.json"


def _record(ticker="ALUA", family="ACCIONES"):
    return with_synthetic_volume_contract({
        "ticker": ticker, "instrument_type": family, "market": "BYMA",
        "currency": "ARS", "settlement": "A-24HS",
        "capability": "READY_PAPER_SPOT", "status": "AVAILABLE",
    })


def _points(count=16, *, at=AT, price_delta=Decimal("0")):
    return [(
        (at - timedelta(minutes=count-i)).isoformat(),
        Decimal("100") + Decimal(i)/2 + (price_delta if i == 0 else 0),
        Decimal(20 if i % 2 == 0 else 10),
    ) for i in range(count)]


def _quote(at=AT, *, ticker="ALUA", family="ACCIONES", market="BYMA", currency="ARS"):
    return engine.Quote(
        ticker, family, "A-24HS", Decimal("107.5"), Decimal("107.5"),
        Decimal("107.55"), Decimal("1000"), Decimal("1000"), at.isoformat(),
        currency=currency, market=market, metadata_source="PPI:OFFLINE_SYNTHETIC_TEST",
        book_at=at.isoformat(), trade_at=at.isoformat(), last_kind="TRADE",
    )


class A222GuardedScalpingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="rc6-a222-spot-")
        self.addCleanup(directory.cleanup)
        self.store = engine.PaperStore(str(Path(directory.name)/"paper.sqlite"))
        catalog.init_schema(self.store)
        scalping.init_schema(self.store)

    def _two_reads(self, record):
        scalping.persist_payload(self.store, record, _points(15, at=AT-timedelta(minutes=1)),
                                 received_at=(AT-timedelta(minutes=1)).isoformat())
        return scalping.persist_payload(self.store, record, _points(), received_at=AT.isoformat())

    def _no_entry(self, record, expected, *, at=AT):
        self.store.add_quote(_quote(at, ticker=record["ticker"], family=record["instrument_type"]))
        self.assertEqual(scalping.evaluate_candidate(self.store, record, at=at), "HOLD")
        # A HOLD must fail before constructing even the simulated broker.
        with patch.dict(os.environ, {"PAPER_SCALPING_MODE": "ACTIVE_PAPER"}), \
             patch.object(runtime, "broker_from_environment", side_effect=AssertionError("BROKER_ON_HOLD")):
            self.assertEqual(scalping.promote_paper_candidate(self.store, record, at=at), expected)
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0], 0)

    def _seed_synthetic_ready_catalog(self):
        # The fixture is a TEST projection of a historical PPI catalog, not
        # proof that any account/instrument is currently eligible to trade.
        catalog_fixture = Path(__file__).parent / "fixtures/ppi_catalog_20260827.json"
        raw = next(item for item in json.loads(catalog_fixture.read_text())['records']
                   if item['ticker'] == 'ALUA' and item['type'] == 'ACCIONES')
        ready = catalog.normalize_record(raw, "A-24HS", AT.isoformat(), "A222_SYNTHETIC_FIXTURE")
        with self.store.connect() as connection:
            catalog.persist(connection, ready)
            connection.execute("""CREATE TABLE IF NOT EXISTS candidate_identity_v2(
                ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,
                settlement TEXT,can_simulate INTEGER,status TEXT,detail TEXT,
                checked_at TEXT)""")
            connection.execute("INSERT INTO candidate_identity_v2 VALUES(?,?,?,?,?,?,?,?,?)",
                ("ALUA", "ACCIONES", "BYMA", "ARS", "A-24HS", 1,
                 "AVAILABLE", "SYNTHETIC_TEST_ONLY", AT.isoformat()))
        selected = catalog.lookup(self.store, "ALUA", "ACCIONES", "A-24HS",
                                  market="BYMA", currency="ARS")
        terms = catalog.quote_terms(selected, now=AT)
        self.assertEqual(terms["opening_block_reason"], "")
        return terms

    def _scalping_buy_candidate(self):
        record = _record()
        self.assertEqual(self._two_reads(record)["state"], "CONFIRMED_INTERVAL_VOLUME")
        terms = self._seed_synthetic_ready_catalog()
        self.store.add_quote(replace(_quote(), **terms))
        self.assertEqual(scalping.evaluate_candidate(self.store, record, at=AT), "BUY_CANDIDATE")
        return record, terms

    def _seed_synthetic_exit_availability(self):
        # This synthetic heartbeat is deliberately restricted to a temporary
        # SQLite test book. It is not an operational exit-reader receipt.
        init_exit(self.store)
        with self.store.connect() as connection:
            connection.execute("INSERT OR REPLACE INTO paper_supervisor_state VALUES(1,?,?,?)",
                               (AT.isoformat(), "RUNNING", "SYNTHETIC_TEST_ONLY"))
            connection.execute("INSERT OR REPLACE INTO paper_exit_reader_state VALUES(1,?,?,?)",
                               (AT.isoformat(), "READY", "SYNTHETIC_TEST_ONLY"))

    def test_a198_funnel_is_only_a_dated_aggregate_and_has_four_exclusive_groups(self):
        evidence = json.loads(SUMMARY.read_text(encoding="utf-8"))
        self.assertEqual(evidence["market_day_art"], "2026-10-29")
        self.assertEqual(evidence["snapshot_scope"], "HISTORICAL_2026_10_29_NOT_CURRENT_RUNTIME")
        self.assertEqual(evidence["total_hold"], 2582)
        self.assertEqual(evidence["reason_counts"], {
            "PPI_VOLUME_CONTRACT_UNVERIFIED": 1272,
            "REJECTED_MUTABLE_CLOSED_POINTS": 857,
            "EMPTY_INTRADAY_PAYLOAD": 452,
            "INTRADAY_CAPABILITY_WARMUP_PENDING": 1,
        })
        self.assertEqual(sum(evidence["reason_counts"].values()), evidence["total_hold"])
        self.assertIn("not actual provider payload pairs", evidence["limits"])

    def test_provider_volume_claims_without_exact_evidence_stay_hold_before_broker(self):
        invalid = {
            "missing": lambda c: c.clear(),
            "other_provider": lambda c: c.update(provider="IOL"),
            "other_endpoint": lambda c: c.update(endpoint="MarketData/Current"),
            "wrong_family": lambda c: c.update(family="CEDEARS"),
            "unverified": lambda c: c.update(verification="PENDING"),
            "no_evidence": lambda c: c.update(evidence_ref=""),
            "future_effective": lambda c: c.update(effective_at="2026-10-06T00:00:00-03:00"),
            "future_known": lambda c: c.update(known_at="2026-10-06T00:00:00-03:00"),
            "unknown_accumulation": lambda c: c.update(accumulation="REVISABLE"),
            "cross_session_reset": lambda c: c.update(reset_rule="UNKNOWN"),
        }
        for name, mutate in invalid.items():
            with self.subTest(volume_claim=name):
                record = _record(ticker="ALUA")
                claim = dict(record["raw"]["intraday_volume_contract"])
                mutate(claim)
                record["raw"]["intraday_volume_contract"] = claim
                with self.assertRaisesRegex(ValueError, "PPI_VOLUME_CONTRACT_UNVERIFIED"):
                    volume_contract(record, AT)
                self.assertEqual(self._two_reads(record)["state"], "PPI_VOLUME_CONTRACT_UNVERIFIED")
                self._no_entry(record, "PPI_VOLUME_CONTRACT_UNVERIFIED")
                # Reset the isolated DB between variants; no false confirmation
                # from another read or reason is allowed to cross a test case.
                with self.store.connect() as connection:
                    for table in ("ppi_intraday_contract_state", "ppi_intraday_points", "market_snapshots", "scalping_candidates"):
                        connection.execute(f"DELETE FROM {table}")

    def test_wrong_volume_units_and_cumulative_resets_do_not_become_quantities(self):
        for unit, accumulation, expected in (
            ("NOMINAL", "INTERVAL", "PPI_QUANTITY_VOLUME_UNAVAILABLE"),
            ("TURNOVER_MONEY", "INTERVAL", "PPI_QUANTITY_VOLUME_UNAVAILABLE"),
            ("QUANTITY", "CUMULATIVE", "PPI_CUMULATIVE_VOLUME_RESET_UNVERIFIED"),
        ):
            with self.subTest(unit=unit, accumulation=accumulation):
                record = _record()
                claim = record["raw"]["intraday_volume_contract"]
                claim.update(unit=unit, accumulation=accumulation)
                if unit == "TURNOVER_MONEY":
                    claim["currency"] = "ARS"
                self.assertEqual(self._two_reads(record)["state"], expected)
                self._no_entry(record, expected)
                with self.store.connect() as connection:
                    for table in ("ppi_intraday_contract_state", "ppi_intraday_points", "market_snapshots", "scalping_candidates"):
                        connection.execute(f"DELETE FROM {table}")

    def test_empty_and_closed_revision_each_revoke_previously_confirmed_source(self):
        record = _record()
        self.assertEqual(self._two_reads(record)["state"], "CONFIRMED_INTERVAL_VOLUME")
        self.store.add_quote(_quote())
        self.assertEqual(scalping.evaluate_candidate(self.store, record, at=AT), "BUY_CANDIDATE")
        after = AT + timedelta(seconds=1)
        rejected = scalping.persist_payload(self.store, record,
            _points(price_delta=Decimal(".01")), received_at=after.isoformat())
        self.assertEqual(rejected["state"], "REJECTED_MUTABLE_CLOSED_POINTS")
        self.assertEqual(rejected["changed"], 1)
        self._no_entry(record, "REJECTED_MUTABLE_CLOSED_POINTS", at=after)
        empty = scalping.persist_payload(self.store, record, [], received_at=(AT+timedelta(seconds=2)).isoformat())
        # The stronger prior closed-minute violation must remain authoritative.
        self.assertEqual(empty["state"], "REJECTED_MUTABLE_CLOSED_POINTS")
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ppi_intraday_revision_forensics").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0], 0)

    def test_empty_after_valid_source_holds_and_forged_warmup_fails_closed(self):
        record = _record()
        self.assertEqual(self._two_reads(record)["state"], "CONFIRMED_INTERVAL_VOLUME")
        after = AT+timedelta(seconds=1)
        self.assertEqual(scalping.persist_payload(self.store, record, [],
                         received_at=after.isoformat())["state"], "EMPTY_INTRADAY_PAYLOAD")
        self._no_entry(record, "EMPTY_INTRADAY_PAYLOAD", at=after)
        with self.store.connect() as connection:
            connection.execute("UPDATE ppi_intraday_contract_state SET state=?",
                               ("INTRADAY_CAPABILITY_WARMUP_PENDING",))
        # A real WARMUP must carry the structured causal capability epoch.
        # Altering just the state is corrupt evidence and is rejected, rather
        # than being quietly interpreted as a legitimate warmup receipt.
        with self.assertRaisesRegex(ValueError, "INTRADAY_CAPABILITY_STATE_INVALID"):
            scalping.evaluate_candidate(self.store, record, at=after)
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0], 0)

    def test_etf_us_equity_and_index_routes_do_not_infer_paper_execution(self):
        etf = strategy_route("ETFS")
        self.assertEqual(etf["engine"], "EQUITY_SPOT")
        self.assertFalse(etf["entry_authority"])
        for family in ("ACCIONES_USA", "INDICES"):
            with self.subTest(family=family):
                route = strategy_route(family)
                self.assertFalse(route["entry_authority"])
                # Known historical alias ACCIONES_USA -> ACCIONES exists in
                # the shared contract resolver; it must not be misrepresented
                # as an independent production-ready foreign-venue route.
                # This observer still grants zero entry authority.
                self.assertEqual(route["generic_equity"], family == "ACCIONES_USA")
                broker = engine.PaperBroker(self.store, clock_fn=lambda:AT.isoformat(), ai_mode="OFF")
                foreign_quote = _quote(family=family, market="NASDAQ" if family == "ACCIONES_USA" else "BYMA",
                                       currency="USD" if family == "ACCIONES_USA" else "ARS")
                self.assertFalse(PaperSessionPolicy().supports(foreign_quote))
                self.assertEqual(broker.decide(foreign_quote)[0], "HOLD")
                if family == "ACCIONES_USA":
                    # Even a synthetic preopen GO cannot produce a NASDAQ
                    # PAPER fill without an exact admitted market contract.
                    with patch.object(preopen, "admission_status", return_value=(True, "")):
                        guarded = engine.PaperBroker(
                            self.store, clock_fn=lambda:AT.isoformat(),
                            admission_guard=preopen.paper_admission_guard, ai_mode="OFF")
                        opened, reason, _ = guarded.admit_paper_candidate(
                            foreign_quote, Decimal(".8"), {})
                    self.assertFalse(opened)
                    self.assertEqual(reason, "PAPER_EXACT_CONTRACT_NOT_READY")
        # Even though ETFS have a generic strategy route, no specific READY
        # catalogue/candidate evidence grants them a PAPER fill.
        etf_quote = _quote(family="ETFS", ticker="SYNTHETIC_ETF")
        with patch.object(preopen, "admission_status", return_value=(True, "")):
            broker = engine.PaperBroker(self.store, clock_fn=lambda:AT.isoformat(),
                                        admission_guard=preopen.paper_admission_guard, ai_mode="OFF")
            opened, reason, _ = broker.admit_paper_candidate(etf_quote, Decimal(".8"), {})
        self.assertFalse(opened)
        self.assertEqual(reason, "PAPER_EXACT_CONTRACT_NOT_READY")
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0], 0)

    def test_synthetic_buy_candidate_stays_blocked_without_paper_preopen_go(self):
        record, _ = self._scalping_buy_candidate()
        # A good signal and a ready synthetic catalog are not authorization;
        # the real preopen guard is exercised with an explicit NO_GO fixture.
        with patch.dict(os.environ, {"PAPER_SCALPING_MODE": "ACTIVE_PAPER",
                                     "PAPER_ECONOMIC_GATE_MODE": "BINDING"}), \
             patch.object(runtime, "now_iso", return_value=AT.isoformat()), \
             patch.object(preopen, "admission_status", return_value=(False, "NO_GO_PAPER_SCALPING")):
            result = scalping.promote_paper_candidate(self.store, record, at=AT)
        self.assertEqual(result, "BLOCKED:NO_GO_PAPER_SCALPING")
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0], 0)

    def test_synthetic_signal_to_paper_fill_to_independent_supervisor_exit(self):
        record, terms = self._scalping_buy_candidate()
        self._seed_synthetic_exit_availability()
        synthetic_env = {"PAPER_SCALPING_MODE": "ACTIVE_PAPER",
                         "PAPER_ECONOMIC_GATE_MODE": "BINDING",
                         "PAPER_SECTOR_CONCENTRATION_POLICY": "OBSERVATION_ONLY",
                         # Synthetic risk/reward profile only. With the
                         # production-default 2% target this same event is
                         # vetoed by binding net economics, as it should be.
                         "PAPER_SCALPING_TARGET_GAIN_PCT": "0.05"}
        with patch.dict(os.environ, synthetic_env), \
             patch.object(runtime, "now_iso", return_value=AT.isoformat()), \
             patch.object(preopen, "admission_status", return_value=(True, "")):
            result = scalping.promote_paper_candidate(self.store, record, at=AT)
        self.assertEqual(result, "OPENED_SIMULATED")
        open_positions = self.store.open_positions()
        self.assertEqual(len(open_positions), 1)
        position = open_positions[0]
        self.assertEqual(position["asset_class"], "ACCIONES")
        self.assertEqual(json.loads(position["features_json"])["execution_style"], "SCALPING_PAPER")
        later = AT + timedelta(minutes=32)
        newer_quote = replace(_quote(at=later), **terms)
        self.store.add_quote(newer_quote)
        exit_broker = engine.PaperBroker(self.store, clock_fn=lambda:later.isoformat(),
                                         admission_guard=preopen.paper_admission_guard, ai_mode="OFF")
        supervisor = PositionExitSupervisor(exit_broker, clock_fn=lambda:later.isoformat(),
                                             session_policy=PaperSessionPolicy())
        verdicts = supervisor.tick()
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0].state, "CLOSED")
        self.assertEqual(verdicts[0].cause, "SCALPING_MAX_HOLD_PAPER")
        self.assertEqual(self.store.open_positions(), [])
        with self.store.connect() as connection:
            fills = connection.execute("SELECT side FROM paper_fills ORDER BY id").fetchall()
            self.assertEqual([row[0] for row in fills], ["BUY_SIMULATED", "SELL_SIMULATED"])
            self.assertEqual(connection.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0], 0)

    def test_default_synthetic_scalping_profile_is_vetoed_by_binding_net_economics(self):
        # The same genuine synthetic BUY_CANDIDATE is insufficient: brokerage,
        # exit fees, slippage and the 2% target fail the shared binding gate.
        # We pin defaults explicitly so this test cannot be accidentally
        # weakened by a developer's local PAPER_SCALPING_TARGET_GAIN_PCT.
        record, _ = self._scalping_buy_candidate()
        self._seed_synthetic_exit_availability()
        with patch.dict(os.environ, {
            "PAPER_SCALPING_MODE": "ACTIVE_PAPER",
            "PAPER_ECONOMIC_GATE_MODE": "BINDING",
            "PAPER_SCALPING_TARGET_GAIN_PCT": "0.02",
            "PAPER_SCALPING_STOP_LOSS_PCT": "0.008",
            "PAPER_MIN_NET_REWARD_RISK": "1.20",
            "PAPER_SECTOR_CONCENTRATION_POLICY": "OBSERVATION_ONLY",
        }), patch.object(runtime, "now_iso", return_value=AT.isoformat()), \
             patch.object(preopen, "admission_status", return_value=(True, "")):
            result = scalping.promote_paper_candidate(self.store, record, at=AT)
        self.assertEqual(result, "BLOCKED:ECONOMICS_BINDING_NET_REWARD_RISK_INSUFFICIENT")
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
