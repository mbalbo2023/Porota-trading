"""Regression contracts for causal, grouped SHADOW attention discovery."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
import unittest

from rc6_dynamic_universe.common import digest
from rc6_dynamic_universe.tradeability import (
    anomaly_events, freeze_preopen, rank_tradeability,
)

CUT = "2026-10-02T20:00:00+00:00"
OPEN = "2026-10-05T13:30:00+00:00"
FROZEN = "2026-10-05T13:15:00+00:00"


def instrument(ticker="A", family="ACCIONES", currency="ARS"):
    return {"ticker": ticker, "instrument_type": family, "family": family,
            "market": "BYMA", "currency": currency, "settlement": "A-24HS", "state": "READY"}


def audited_sessions():
    result = []
    day = date(2026, 10, 2)
    while len(result) < 20:
        if day.weekday() < 5:
            result.append(day.isoformat())
        day -= timedelta(days=1)
    return sorted(result)


def daily(asset, value=100):
    return [{**asset, "session": day, "observed_at": day + "T19:55:00+00:00",
             "published_at": day + "T19:56:00+00:00", "source": "AUDITED_PPI_HISTORY",
             "volume": value, "volume_unit": "SHARES", "turnover": value * 10,
             "turnover_currency": asset["currency"], "trades": 10, "range_bps": 80,
             "interarrival_seconds": 60} for day in audited_sessions()]


def quotes(asset):
    return [{**asset, "session": "2026-10-02", "observed_at": f"2026-10-02T19:59:{seconds:02d}+00:00",
             "published_at": f"2026-10-02T19:59:{seconds+1:02d}+00:00", "source": "PPI_CURRENT_BOOK",
             "usable": True, "spread_bps": 10, "depth_units": 20, "depth_unit": "SHARES",
             "slippage_bps": 2} for seconds in (10, 20, 30)]


def ranking(assets=None, history=None, observations=None, **kwargs):
    assets = assets or [instrument()]
    history = history if history is not None else [row for asset in assets for row in daily(asset)]
    observations = observations if observations is not None else [row for asset in assets for row in quotes(asset)]
    return rank_tradeability(assets, history, observations, cutoff=CUT, sessions=audited_sessions(), **kwargs)


def intraday(asset, session, minute, *, volume=100, price=100, trades=10, spread=20, depth=20):
    base = datetime.fromisoformat(session + "T13:30:00+00:00") + timedelta(minutes=minute)
    return {**asset, "session": session, "minute_of_session": minute,
            "observed_at": base.isoformat(), "published_at": (base + timedelta(seconds=1)).isoformat(),
            "source": "PPI_INTRADAY", "cumulative_volume": volume, "volume_unit": "SHARES",
            "price": price, "trades": trades, "spread_bps": spread,
            "depth_units": depth, "depth_unit": "SHARES", "usable": True}


def profiles(asset):
    result = []
    for index, session in enumerate(audited_sessions()[-5:]):
        result.extend([intraday(asset, session, 25, volume=80, price=100),
                       intraday(asset, session, 30, volume=100, price=100 + index * 0.1)])
    return result


class TradeabilityTests(unittest.TestCase):
    def test_pit_activity_units_and_attention_score_are_not_directional(self):
        row = ranking()["rows"][0]
        self.assertTrue(row["tradeable"])
        self.assertEqual(row["components"]["activity5"]["value"], 1)
        self.assertEqual(row["components"]["activity20"]["value"], 1)
        self.assertEqual(row["components"]["median_volume"]["value"], 100)
        self.assertEqual(row["components"]["median_turnover"]["unit"], "ARS")
        self.assertEqual(row["components"]["depth_paper_multiple"]["value"], 20)
        self.assertFalse(row["score_is_probability"])
        self.assertFalse(row["directional_authority"])
        self.assertIn("spread_quality", row["score_components"])

    def test_future_publication_and_future_return_do_not_change_rank(self):
        asset = instrument()
        baseline = ranking()
        history = daily(asset)
        future = dict(history[-1], volume=999999, published_at="2026-10-05T21:00:00+00:00")
        history.append(future)
        for row in history:
            row["future_return"] = 999
        report = ranking(history=history)
        self.assertEqual(report["rows"][0]["components"], baseline["rows"][0]["components"])
        self.assertEqual(report["rows"][0]["tradeability_score"], baseline["rows"][0]["tradeability_score"])
        self.assertEqual(report["rejected_future_count"], 1)
        self.assertNotEqual(report["inputs_hash"], baseline["inputs_hash"])

    def test_missing_history_stays_unknown_and_catalog_is_preserved(self):
        assets = [instrument("A"), instrument("B"), instrument("BOND", "BONOS")]
        report = ranking(assets, history=[], observations=[])
        self.assertEqual(len(report["rows"]), 3)
        for row in report["rows"]:
            self.assertIsNone(row["components"]["activity20"]["value"])
            self.assertIsNone(row["components"]["median_volume"]["value"])
            self.assertFalse(row["tradeable"])
        self.assertEqual([asset["state"] for asset in assets], ["READY"] * 3)
        self.assertIn("SPECIALIZED_LIFECYCLE", report["rows"][-1]["reason_codes"])

    def test_exact_calendar_missing_data_is_not_zero_activity(self):
        asset = instrument()
        history = daily(asset)[:-1]
        row = ranking(history=history)["rows"][0]
        self.assertIsNone(row["components"]["activity5"]["value"])
        self.assertEqual(row["components"]["activity5"]["observed_sessions"], 4)
        self.assertNotIn("NO_RECENT_TRADES", row["reason_codes"])
        with self.assertRaisesRegex(ValueError, "AUDITED_SESSION_AFTER_CUTOFF"):
            rank_tradeability([asset], history, quotes(asset), cutoff=CUT,
                              sessions=audited_sessions() + ["2026-10-05"])

    def test_measured_inactivity_has_native_reason(self):
        history = daily(instrument())
        for row in history[-5:]:
            row.update(volume=0, trades=0, turnover=0)
        report = ranking(history=history)
        self.assertEqual(report["rows"][0]["components"]["activity5"]["value"], 0)
        self.assertIn("NO_RECENT_TRADES", report["rows"][0]["reason_codes"])

    def test_actual_concentration_is_grouped_and_currency_qualified(self):
        assets = [instrument("A"), instrument("B"), instrument("C"),
                  instrument("D", currency="USD"), instrument("E", family="CEDEARS")]
        history = [row for asset, volume in zip(assets, (70, 20, 10, 100000, 100000)) for row in daily(asset, volume)]
        report = ranking(assets, history=history)
        curve = next(curve for curve in report["concentration_curves"]
                     if curve["group"] == ["ACCIONES", "BYMA", "ARS"] and curve["metric"] == "median_volume")
        self.assertEqual([point["cumulative_share"] for point in curve["points"]], [0.7, 0.9, 1])
        self.assertEqual(curve["total"], 100)
        self.assertEqual(len(report["concentration_curves"]), 6)
        self.assertEqual([row["rank"] for row in report["rows"][:3]], [1, 2, 3])

    def test_nominal_or_wrong_currency_metrics_are_not_fabricated(self):
        history = daily(instrument())
        for row in history:
            row["volume_unit"] = "NOMINAL"
            row["turnover_currency"] = "USD"
        report = ranking(history=history)
        row = report["rows"][0]
        self.assertIsNone(row["components"]["median_volume"]["value"])
        self.assertIsNone(row["components"]["median_turnover"]["value"])
        self.assertIn("LIQUIDITY_UNITS_NO_VERIFICADO", row["reason_codes"])
        self.assertEqual(report["concentration_curves"], [])

    def test_source_clocks_are_required_and_not_replaced(self):
        history = daily(instrument())
        for row in history:
            row.pop("published_at")
        report = ranking(history=history)
        self.assertEqual(report["rejected_inputs"]["history"]["SOURCE_TIMESTAMP_UNVERIFIED"], 20)
        self.assertIsNone(report["rows"][0]["components"]["activity20"]["value"])

    def test_spread_depth_freshness_native_reasons(self):
        observation = quotes(instrument())
        for row in observation:
            row.update(spread_bps=100, depth_units=0.5)
        row = ranking(observations=observation)["rows"][0]
        self.assertIn("SPREAD_TOO_WIDE", row["reason_codes"])
        self.assertIn("INSUFFICIENT_DEPTH", row["reason_codes"])
        for quote in observation:
            quote["observed_at"] = "2026-10-02T18:00:00+00:00"
        row = ranking(observations=observation)["rows"][0]
        self.assertIn("STALE_QUOTES", row["reason_codes"])

    def test_preopen_freeze_is_canonical_immutable_and_previous_session_only(self):
        report = ranking()
        frozen = freeze_preopen(report, frozen_at=FROZEN, session_open=OPEN, capacity_fingerprint="capacity1")
        initial = frozen.payload
        self.assertEqual(frozen.digest, digest(initial))
        self.assertEqual(initial["rows"], report["rows"])
        initial["rows"].clear()
        report["rows"].clear()
        self.assertEqual(len(frozen.payload["rows"]), 1)
        with self.assertRaises(Exception):
            frozen.digest = "tampered"
        changed = freeze_preopen(ranking(config={"min_activity5": 0.8}), frozen_at=FROZEN,
                                 session_open=OPEN, capacity_fingerprint="capacity2")
        self.assertNotEqual(changed.digest, frozen.digest)
        with self.assertRaisesRegex(ValueError, "PREOPEN_CUT_FREEZE_OPEN_ORDER_REQUIRED"):
            freeze_preopen(ranking(), frozen_at=OPEN, session_open=OPEN, capacity_fingerprint="c")
        report = ranking()
        report["cutoff"] = "2026-10-05T13:00:00+00:00"
        with self.assertRaisesRegex(ValueError, "PREVIOUS_SESSION_ONLY"):
            freeze_preopen(report, frozen_at=FROZEN, session_open=OPEN, capacity_fingerprint="c")

    def test_family_metadata_cannot_override_identity_or_group(self):
        asset = instrument("BOND", "BONOS")
        asset["family"] = "ACCIONES"
        report = ranking([asset])
        row = report["rows"][0]
        self.assertEqual(row["family"], "BONOS")
        self.assertEqual(row["group"][0], "BONOS")
        self.assertFalse(row["tradeable"])
        self.assertIn("FAMILY_IDENTITY_MISMATCH", row["reason_codes"])
        self.assertIn("SPECIALIZED_LIFECYCLE", row["reason_codes"])
        self.assertEqual(report["rejected_inputs"]["history"]["FAMILY_IDENTITY_MISMATCH"], 20)

    def test_canonical_family_aliases_are_not_contradictions(self):
        asset = instrument("AAPL", "CEDEAR")
        asset["family"] = "CEDEARS"
        row = ranking([asset])["rows"][0]
        self.assertEqual(row["family"], "CEDEARS")
        self.assertTrue(row["tradeable"])

    def test_source_event_cannot_be_available_before_event_clock(self):
        history = daily(instrument())
        for row in history:
            row["published_at"] = row["session"] + "T19:54:00+00:00"
        report = ranking(history=history)
        self.assertEqual(report["rejected_inputs"]["history"]["SOURCE_CLOCK_ORDER_INVALID"], 20)
        self.assertIsNone(report["rows"][0]["components"]["activity20"]["value"])

    def test_insufficient_audited_calendar_and_mixed_depth_units_are_unknown(self):
        asset = instrument()
        report = rank_tradeability([asset], daily(asset), quotes(asset), cutoff=CUT,
                                   sessions=audited_sessions()[-5:])
        row = report["rows"][0]
        self.assertEqual(row["components"]["activity5"]["value"], 1)
        self.assertIsNone(row["components"]["activity20"]["value"])
        observations = quotes(asset)
        observations[0]["depth_unit"] = "UNITS"
        row = ranking(observations=observations)["rows"][0]
        self.assertIsNone(row["components"]["depth_paper_multiple"]["value"])
        self.assertIn("DEPTH_NO_VERIFICADO", row["reason_codes"])


    def test_frozen_provenance_rejects_backdated_future_evidence(self):
        report = ranking()
        report["rows"][0]["provenance"]["evidence"][0]["published_at"] = FROZEN
        with self.assertRaisesRegex(ValueError, "PREOPEN_FUTURE_EVIDENCE_REJECTED"):
            freeze_preopen(report, frozen_at=FROZEN, session_open=OPEN, capacity_fingerprint="c")

    def test_cedear_features_keep_individual_clocks_and_unknowns(self):
        asset = instrument("AAPL", "CEDEARS")
        asset["cedear_features"] = {
            "ratio": {"value": 20, "source": "AUDITED_RATIO", "observed_at": "2026-10-02T19:00:00+00:00",
                      "published_at": "2026-10-02T19:01:00+00:00", "max_age_seconds": 86400},
            "ccl": {"value": 1000, "source": "FUTURE_PROXY", "observed_at": OPEN, "published_at": OPEN},
        }
        row = ranking([asset])["rows"][0]
        self.assertTrue(row["tradeable"])
        self.assertEqual(row["cedear_features"]["ratio"]["value"], 20)
        self.assertEqual(row["cedear_features"]["ratio"]["freshness_status"], "FRESH")
        self.assertIsNone(row["cedear_features"]["ccl"]["value"])
        self.assertEqual(row["cedear_features"]["underlying_us_session"]["status"], "NO_VERIFICADO")


class DiscoveryTests(unittest.TestCase):
    def test_same_time_profile_and_acceleration_can_promote_outside_preopen(self):
        asset = instrument("OUTSIDE")
        current = [intraday(asset, "2026-10-05", 25, volume=80),
                   intraday(asset, "2026-10-05", 30, volume=300)]
        report = anomaly_events(profiles(asset), current, as_of="2026-10-05T14:00:02+00:00")
        event = report["events"][0]
        self.assertEqual(event["features"]["rvol"], 3)
        self.assertEqual(event["features"]["volume_acceleration"], 11)
        self.assertTrue(event["promotion_candidate"])
        self.assertFalse(event["entry_authority"])
        self.assertTrue(event["warmup_required"])
        self.assertEqual(event["action"], "PROMOTION_ONLY")
        self.assertEqual(event["identity"][0], "OUTSIDE")

    def test_price_shock_uses_same_horizon_volatility_and_structural_changes(self):
        asset = instrument()
        current = [intraday(asset, "2026-10-05", 25, volume=80, trades=0, spread=40, depth=10),
                   intraday(asset, "2026-10-05", 30, volume=100, price=110, trades=1, spread=10, depth=30)]
        event = anomaly_events(profiles(asset), current, as_of="2026-10-05T14:00:02+00:00")["events"][0]
        self.assertGreater(event["features"]["normalized_price_shock"], 3)
        self.assertTrue(event["features"]["inactive_to_active"])
        self.assertEqual(set(event["reason_codes"]), {"PRICE_SHOCK", "INACTIVE_TO_ACTIVE", "SPREAD_COMPRESSION", "DEPTH_IMPROVEMENT"})

    def test_future_information_cannot_alter_contemporaneous_event(self):
        asset = instrument()
        history = profiles(asset)
        current = [intraday(asset, "2026-10-05", 25, volume=80), intraday(asset, "2026-10-05", 30, volume=300)]
        as_of = "2026-10-05T14:00:02+00:00"
        baseline = anomaly_events(history, current, as_of=as_of)
        current.append(intraday(asset, "2026-10-05", 31, volume=999999, price=999999))
        history.append(intraday(asset, "2026-10-06", 30, volume=999999))
        changed = anomaly_events(history, current, as_of=as_of)
        self.assertEqual(changed["events"], baseline["events"])
        self.assertEqual(changed["rejected_future_count"], 2)

    def test_no_same_minute_profile_or_nominal_volume_stays_unknown(self):
        asset = instrument()
        historical = profiles(asset)
        for row in historical:
            row["minute_of_session"] += 1
        current = [intraday(asset, "2026-10-05", 30, volume=999999)]
        event = anomaly_events(historical, current, as_of="2026-10-05T14:00:02+00:00")["events"][0]
        self.assertIsNone(event["features"]["rvol"])
        self.assertIsNone(event["features"]["volume_acceleration"])
        self.assertIsNone(event["features"]["normalized_price_shock"])
        self.assertFalse(event["promotion_candidate"])
        current[0]["volume_unit"] = "NOMINAL"
        event = anomaly_events(profiles(asset), current, as_of="2026-10-05T14:00:02+00:00")["events"][0]
        self.assertIsNone(event["features"]["rvol"])

    def test_stale_or_specialized_source_cannot_promote(self):
        asset = instrument()
        current = [intraday(asset, "2026-10-05", 25, volume=80), intraday(asset, "2026-10-05", 30, volume=300)]
        event = anomaly_events(profiles(asset), current, as_of="2026-10-05T15:00:00+00:00")["events"][0]
        self.assertIn("RVOL_ANOMALY", event["reason_codes"])
        self.assertIn("STALE_QUOTES", event["rejection_reason_codes"])
        self.assertFalse(event["promotion_candidate"])
        asset = instrument("OPTION", "OPCIONES")
        current = [intraday(asset, "2026-10-05", 25, volume=80), intraday(asset, "2026-10-05", 30, volume=300)]
        event = anomaly_events(profiles(asset), current, as_of="2026-10-05T14:00:02+00:00")["events"][0]
        self.assertIn("SPECIALIZED_LIFECYCLE", event["rejection_reason_codes"])
        self.assertFalse(event["promotion_candidate"])

    def test_anomaly_family_metadata_cannot_override_identity(self):
        asset = instrument("BOND", "BONOS")
        asset["family"] = "ACCIONES"
        observations = [intraday(asset, "2026-10-05", 25, volume=80),
                        intraday(asset, "2026-10-05", 30, volume=400)]
        report = anomaly_events(profiles(asset), observations, as_of="2026-10-05T14:00:02+00:00")
        self.assertEqual(report["events"], [])
        self.assertEqual(report["rejected_inputs"]["observations"]["FAMILY_IDENTITY_MISMATCH"], 2)


    def test_configuration_is_versioned_shadow_and_outputs_are_json_safe(self):
        first = ranking()
        second = ranking(config={"min_activity5": 0.9})
        self.assertNotEqual(first["config_fingerprint"], second["config_fingerprint"])
        self.assertEqual(first["real_orders_sent"], 0)
        json.dumps(first, allow_nan=False)
        with self.assertRaisesRegex(ValueError, "SHADOW_OOS_REQUIRED"):
            ranking(config={"evaluation": "PRODUCTION"})
        with self.assertRaisesRegex(ValueError, "UNKNOWN_SHADOW_HYPOTHESIS"):
            ranking(config={"profit_threshold": 0.9})


if __name__ == "__main__":
    unittest.main()
