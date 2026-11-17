"""A510: BYMA traded/no-settlement history gap, offline-only synthetic regressions."""

from datetime import datetime
from decimal import Decimal
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from zoneinfo import ZoneInfo

import al_historical_ingest as legacy
import ba_data912_history as data912
import cu_history_store_v2_hf6 as history
import ek_history_freshness_metrics_rc5 as metrics
from ak_byma_t1_source_clock import estado_negociacion_fuente_byma
from bl_candle_engine import Bar, CandleArchive, Series, init_schema
from cv_history_store_adapter_hf6 import HistoricalStore
import rc6_local_eod_history as local_eod


ART = ZoneInfo("America/Argentina/Buenos_Aires")
KNOWN = "2026-11-11T21:00:00+00:00"


def _candle(day, *, family="ACCIONES", settlement="A-24HS", market="BYMA"):
    return history.Candle(
        symbol="A510", instrument_type=family, market=market,
        currency="ARS", settlement=settlement, date=day,
        open=100, high=103, low=99, close=101, volume=1000,
        source="IOL", price_basis="RAW", adjustment_basis="RAW_NO_ADJUSTMENT",
        observed_at=KNOWN, volume_kind="QUANTITY",
    )


def _data912_row(day):
    return {"date":day,"o":100,"h":103,"l":99,"c":101,"v":1000,
            "currency":"ARS","volume_kind":"QUANTITY"}


class TestA510TradedHistoryDay(unittest.TestCase):
    def test_history_accepted_traded_nov6_but_closed_nov9_nov10(self):
        # Source-only A777 entitlement is attached to an exact 5-key candle.
        _candle("2026-11-06").normalized().validate()
        for closed in ("2026-11-09", "2026-11-10"):
            with self.assertRaisesRegex(ValueError, "HISTORY_SESSION_NOT_OPERATIONAL"):
                _candle(closed).normalized().validate()

    def test_nov6_never_admitted_without_exact_t1_market_and_family(self):
        disallowed = (
            {"family": "ACCIONES", "settlement": "CI"},
            {"family": "CAUCIONES", "settlement": "A-24HS"},
            {"family": "ETFS", "settlement": "A-24HS"},
            {"family": "ACCIONES", "settlement": "A-48HS"},
        )
        for kwargs in disallowed:
            with self.subTest(**kwargs), self.assertRaisesRegex(
                    ValueError, "HISTORY_SESSION_NOT_OPERATIONAL"):
                _candle("2026-11-06", **kwargs).normalized().validate()
        self.assertEqual(estado_negociacion_fuente_byma(
            datetime(2026, 11, 6, tzinfo=ART).date(), market="BYMA",
            family="ACCIONES", settlement="A-24HS"), "LIMITED_T1_SOURCE_ONLY")

    def test_numeric_validator_unknown_five_key_cannot_admit_nov6(self):
        with self.assertRaisesRegex(ValueError, "HISTORY_SESSION_NOT_OPERATIONAL"):
            history.validate_daily_values(date="2026-11-06", open=100, high=103,
                low=99, close=101, volume=1000, known_at=KNOWN, market="BYMA")

    def test_data912_without_verified_settlement_remains_fail_closed(self):
        rows = [_data912_row(day) for day in
                ("2026-11-05", "2026-11-06", "2026-11-09", "2026-11-10")]
        normalized, rejected = data912._normalize(rows, "2026-11-05", as_of=KNOWN,
                                                   return_rejections=True)
        self.assertEqual([row[0] for row in normalized], ["2026-11-05"])
        self.assertEqual([entry["reason"] for entry in rejected],
                         ["SESSION_NOT_OPERATIONAL"] * 3)

    def test_legacy_generic_historical_day_without_settlement_is_not_promoted(self):
        saturday = datetime(2026, 11, 7, 10, tzinfo=ART)
        self.assertEqual(legacy.fecha_esperada_historica(saturday).isoformat(), "2026-11-05")
        wednesday = datetime(2026, 11, 11, 20, tzinfo=ART)
        self.assertEqual(legacy.calcular_ruedas_atrasadas("2026-11-05", wednesday), 1)

    def test_per_five_key_freshness_counts_nov6_t1_only_without_ci_false_stale(self):
        start = datetime(2026, 11, 5, tzinfo=ART).date()
        finish = datetime(2026, 11, 12, tzinfo=ART).date()
        self.assertEqual(metrics._business_gap_2026(start, finish, "ACCIONES",
            market="BYMA", settlement="A-24HS"), 3)  # 06, 11, 12
        self.assertEqual(metrics._business_gap_2026(start, finish, "ACCIONES",
            market="BYMA", settlement="CI"), 2)  # 11, 12
        self.assertEqual(metrics._freshness(start, finish, "ACCIONES",
            market="BYMA", settlement="A-24HS"), ("STALE", "3"))
        self.assertEqual(metrics._freshness(start, finish, "ACCIONES",
            market="BYMA", settlement="CI"), ("FRESH", "2"))

    def test_local_eod_replay_accepts_nov6_t1_samples_only_without_full_ohlc(self):
        # Isolated synthetic SQLite; no provider calls or live source files.
        with TemporaryDirectory(prefix="a510-local-") as work:
            source = HistoricalStore(str(Path(work) / "hot.db"))
            init_schema(source)
            after = "2026-11-11T23:00:00+00:00"  # normal BYMA date after close
            with source.connect() as connection:
                connection.executescript(
                    "CREATE TABLE observer_state(id INTEGER PRIMARY KEY, mode TEXT, "
                    "real_orders_sent INTEGER, session_state TEXT, heartbeat_at TEXT);"
                    "CREATE TABLE paper_positions(status TEXT);")
                connection.execute("INSERT INTO observer_state VALUES(1,?,?,?,?)",
                                   ("PRODUCTION_PAPER", 0, "MARKET_CLOSED", after))
            for settlement in ("A-24HS", "CI"):
                series = Series("GGAL", "ACCIONES", "BYMA", "ARS", settlement,
                    "1m", "PRODUCTION_PAPER_SNAPSHOTS", "RAW",
                    "OBSERVED_NOMINAL_PRICE", "TRADE_SAMPLES", "UNKNOWN")
                bar = Bar("2026-11-06T14:00:00+00:00", *([Decimal("100")]*4),
                          samples=1, quality="SAMPLED")
                CandleArchive(source).put(series, bar,
                    known_at="2026-11-06T14:01:00+00:00")
            target = Path(work) / "cold.db"
            result = local_eod.consolidate(source.path, target,
                as_of=after, free_bytes=local_eod.RESERVE)
            self.assertEqual(result["state"], "LOCAL_EOD_READY")
            self.assertEqual(result["imported"], 1)
            self.assertEqual(result["rejected"], 1)
            self.assertEqual(result["history_v2_writes"], False)
            self.assertEqual(result["provider_requests"], 0)
            with sqlite3.connect(target) as connection:
                self.assertEqual(connection.execute(
                    "SELECT COUNT(*) FROM history_local_daily_v1").fetchone()[0], 1)
                self.assertEqual(connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE name='history_canonical_v2'").fetchone()[0], 0)

    def test_local_eod_asof_nov6_remains_blocked_by_paper_calendar(self):
        response = local_eod.consolidate("/does-not-exist", "/not-used",
            as_of="2026-11-06T21:00:00+00:00", free_bytes=local_eod.RESERVE)
        self.assertEqual(response["state"], "NOT_DUE")
