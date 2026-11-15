"""OFFHOST PPI EOD proof uses persisted full-key RAW rows, never a clock alone.

The production observer imports the network SDK, so only its actual local
functions are compiled. This test does not make provider, host or order calls.
"""

import ast
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import sqlite3
import sys
from types import ModuleType, SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from co_market_sessions_hf6 import byma_eod_publication_floor


SOURCE = Path(__file__).resolve().parents[1] / "bf_production_paper_observer.py"
TZ = ZoneInfo("America/Argentina/Buenos_Aires")
SESSION = date(2026, 11, 2)
NOW = datetime.fromisoformat("2026-11-02T18:45:00-03:00")
GGAL = ("GGAL", "ACCIONES", "BYMA", "ARS", "CI")
GGAL_USD = ("GGAL", "ACCIONES", "BYMA", "USD_MEP", "CI")


def _functions(*names):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    found = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    namespace = {"TZ": TZ, "datetime": datetime,
                 "byma_eod_publication_floor": byma_eod_publication_floor}
    exec(compile(ast.Module(body=[found[name] for name in names], type_ignores=[]),
                 str(SOURCE), "exec"), namespace)
    return namespace


class _History:
    def __init__(self, database):
        self.database = database

    def connect(self):
        return sqlite3.connect(self.database)


@pytest.fixture
def history(tmp_path):
    storage = _History(tmp_path / "raw-history.db")
    with storage.connect() as conn:
        conn.execute("""CREATE TABLE history_canonical_v2(
            symbol TEXT, instrument_type TEXT, market TEXT, currency TEXT,
            settlement TEXT, date TEXT, price_basis TEXT, adjustment_basis TEXT,
            source TEXT, version_known_at TEXT)""")
    return storage


def _row(history, identity=GGAL, *, day="2026-11-02",
         basis="RAW", adjustment="RAW_NO_ADJUSTMENT",
         source="PPI_PRODUCTION_HISTORY", known="2026-11-02T18:20:00-03:00"):
    with history.connect() as conn:
        conn.execute("INSERT INTO history_canonical_v2 VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (*identity, day, basis, adjustment, source, known))


def test_eod_helper_certifies_only_persisted_exact_full_key_raw_at_floor(history):
    _row(history)
    verified = _functions("_verified_ppi_eod_keys")["_verified_ppi_eod_keys"]
    assert verified(history, [GGAL, GGAL_USD], SESSION, NOW) == 1
    _row(history, GGAL_USD)
    assert verified(history, [GGAL, GGAL_USD], SESSION, NOW) == 2
    assert verified(history, [GGAL, GGAL, GGAL_USD], SESSION, NOW) == 2


@pytest.mark.parametrize("override", [
    {"day": "2026-10-30"},
    {"source": "IOL"},
    {"basis": "ADJUSTED"},
    {"adjustment": "SPLIT_ADJUSTED"},
    {"known": "2026-11-02T18:14:59-03:00"},
    {"known": "2026-11-02T18:46:00-03:00"},
    {"known": "2026-11-02T18:22:00"},
    {"known": "not-a-clock"},
])
def test_eod_helper_rejects_noncanonical_or_unpublished_row(history, override):
    _row(history, **override)
    verified = _functions("_verified_ppi_eod_keys")["_verified_ppi_eod_keys"]
    assert verified(history, [GGAL], SESSION, NOW) == 0


def test_eod_helper_rejects_wrong_session_and_early_or_naive_clock(history):
    _row(history)
    verified = _functions("_verified_ppi_eod_keys")["_verified_ppi_eod_keys"]
    assert verified(history, [GGAL], date(2026, 11, 3), NOW) == 0
    assert verified(history, [GGAL], SESSION,
                    datetime.fromisoformat("2026-11-02T18:08:00-03:00")) == 0
    assert verified(history, [GGAL], SESSION,
                    datetime.fromisoformat("2026-11-02T18:45:00")) == 0


def test_eod_helper_rejects_ambiguous_canonical_matches(history):
    _row(history)
    _row(history, source="IOL")
    verified = _functions("_verified_ppi_eod_keys")["_verified_ppi_eod_keys"]
    assert verified(history, [GGAL], SESSION, NOW) == 0


def _fake_module(monkeypatch, name, **functions):
    module = ModuleType(name)
    module.__dict__.update(functions)
    monkeypatch.setitem(sys.modules, name, module)


def _history_cycle(monkeypatch, history, tmp_path, *, source="PPI_PRODUCTION_HISTORY",
                   publish=True, targets=True, ambiguous=False,
                   provider_ambiguous=False, large_currency_detail=False):
    """Exercise the actual locked producer without opening any PPI connection."""
    observer_db = tmp_path / "observer.db"
    with sqlite3.connect(observer_db) as conn:
        conn.execute("""CREATE TABLE source_sync(
          source TEXT PRIMARY KEY,status TEXT,last_attempt_at TEXT,
          last_success_at TEXT,items INTEGER,detail TEXT)""")
        conn.execute("""CREATE TABLE financial_instrument_catalog(
          ticker TEXT,instrument_type TEXT,settlement TEXT,status TEXT)""")
    _row(history, day="2026-10-30", known="2026-10-30T18:20:00-03:00")

    coverage = {"denominator": 1, "covered": 1, "sufficient": 1, "missing": 0,
                "cohort_sha256": "test", "consumer": "PPI_HISTORY_INGEST_MONITOR",
                "as_of": NOW.isoformat(), "session": SESSION.isoformat(),
                "by_currency": {"ARS": 1}}
    if large_currency_detail:
        coverage["by_currency"] = {f"CURRENCY_{number:03}": number
                                   for number in range(150)}
    _fake_module(monkeypatch, "rc6_data_retention",
                 bounded_daily_start=lambda requested, _: requested)
    _fake_module(monkeypatch, "cu_history_store_v2_hf6",
                 init_schema=lambda _: None, coverage_inventory=lambda *_a, **_kw: coverage)
    _fake_module(monkeypatch, "cq_history_attempt_ledger_hf6",
                 init_schema=lambda _: None,
                 HistoryAttempt=lambda **kwargs: SimpleNamespace(**kwargs),
                 append_attempt=lambda _store, _attempt: None)
    _fake_module(monkeypatch, "bl_candle_engine",
                 canonical=lambda _: "INTERNAL_ROW_KEY")
    _fake_module(monkeypatch, "fb_raw_evidence_exact_v1",
                 enabled=lambda: True, archive_wrapper=lambda **_kw: None)

    def ingest(_store, **_kwargs):
        if publish:
            _row(history, source=source)
        return {"full_ohlc_rows": 1, "storage_quality": "VALID_PAYLOAD",
                "rejected_rows": 0}

    _fake_module(monkeypatch, "ct_ppi_history_salvage_hf6",
                 ingest_ppi_payload=ingest)

    class _FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz or TZ)

    class _Store:
        def __init__(self):
            self.events = []

        def connect(self):
            return sqlite3.connect(observer_db)

        def event(self, kind, detail):
            self.events.append((kind, detail))

    store = _Store()
    calls = []

    def read_history(*args):
        calls.append(args)
        return {"rows": [{"date": "2026-11-02", "close": 10}]}

    env = _functions("_history_batch_semantics", "_verified_ppi_eod_keys",
                     "_sync_state", "_download_histories_locked")
    env.update(date=date, datetime=_FixedClock, json=json,
               timedelta=__import__("datetime").timedelta,
               HISTORY_BATCH_LIMIT=5, now_iso=lambda: NOW.isoformat(),
               _history_end_date=lambda: SESSION,
               _historical_targets=lambda _store: [GGAL] if targets else [],
               _exact_history_target=lambda _store, target: None if ambiguous else
                   (target, {"raw": {"_provider_instrument_type": "ACCIONES"}}),
               _history_provider_instrument_type=lambda *_a: "ACCIONES",
               _ppi_history_request_is_ambiguous=lambda *_a: provider_ambiguous,
               _health=lambda *_a, **_kw: None)
    result = env["_download_histories_locked"](
        SimpleNamespace(history=read_history), store, history)
    with store.connect() as conn:
        row = conn.execute("SELECT status,last_success_at,detail FROM source_sync "
                           "WHERE source='PPI_PRODUCTION_HISTORY'").fetchone()
    return result, row, calls, store.events


def test_real_producer_marks_verified_only_after_full_key_publication(monkeypatch, history, tmp_path):
    amount, row, calls, _events = _history_cycle(monkeypatch, history, tmp_path)
    assert amount == 1 and len(calls) == 1
    assert row[0] == "VERDE" and row[1] == NOW.isoformat()
    assert "source_session_verified=2026-11-02:1/1" in row[2]


def test_bounded_source_detail_retains_consumer_contract_before_large_currency_data(
        monkeypatch, history, tmp_path):
    _amount, row, calls, _events = _history_cycle(
        monkeypatch, history, tmp_path, large_currency_detail=True)
    assert len(calls) == 1 and row[0] == "VERDE"
    for token in ("source_session_verified=2026-11-02:1/1",
                  "source_raw_provenance=OBSERVER_PPI_HISTORY_CANONICAL_V2_RAW_FULL_KEY",
                  "Lote aceptado=1/1", "cohort=test",
                  "consumer=PPI_HISTORY_INGEST_MONITOR",
                  "session_requested=2026-11-02", "readiness=NONE"):
        assert token in row[2], token


@pytest.mark.parametrize("source,publish", [
    ("IOL", True),
    ("PPI_PRODUCTION_HISTORY", False),
])
def test_real_producer_degrades_without_genuine_session_raw(
        monkeypatch, history, tmp_path, source, publish):
    _amount, row, calls, _events = _history_cycle(
        monkeypatch, history, tmp_path, source=source, publish=publish)
    assert len(calls) == 1
    assert row[0] == "AMARILLO" and row[1] is None
    assert "source_session_verified=2026-11-02:0/1" in row[2]


def test_real_producer_empty_batch_cannot_go_green_from_catalog_coverage(
        monkeypatch, history, tmp_path):
    _amount, row, calls, _events = _history_cycle(
        monkeypatch, history, tmp_path, targets=False)
    assert calls == []
    assert row[0] == "AMARILLO" and row[1] is None
    assert "source_session_verified=2026-11-02:0/0" in row[2]


def test_real_producer_missing_full_identity_never_sends_provider_request(
        monkeypatch, history, tmp_path):
    _amount, row, calls, events = _history_cycle(
        monkeypatch, history, tmp_path, ambiguous=True)
    assert calls == []
    assert row[0] == "ROJO" and row[1] is None
    assert "source_session_verified=2026-11-02:0/0" in row[2]
    assert any("HISTORY_FULL_IDENTITY_MISSING" in detail for _, detail in events)


def test_real_producer_rejects_ambiguous_provider_identity_before_get(
        monkeypatch, history, tmp_path):
    _amount, row, calls, events = _history_cycle(
        monkeypatch, history, tmp_path, provider_ambiguous=True)
    assert calls == []
    assert row[0] == "ROJO" and row[1] is None
    assert "source_session_verified=2026-11-02:0/1" in row[2]
    assert any("HISTORY_PROVIDER_IDENTITY_AMBIGUOUS" in detail
               for _, detail in events)


@pytest.mark.parametrize("clock,expected", [
    ("17:00", None),
    ("18:14", None),
    ("18:15", SESSION),
    ("18:16", SESSION),
])
def test_history_end_date_uses_publication_floor_not_spot_close(clock, expected):
    gate = _functions("_history_end_date")
    gate.update(date=date, timedelta=timedelta,
                MARKET_CLOSE_HOUR=17, MARKET_CLOSE_MINUTE=0,
                BYMA_CALENDAR_AUDITED_YEAR=2026,
                _business_day=lambda value: value.weekday() < 5)
    at = datetime.fromisoformat(f"2026-11-02T{clock}:00-03:00")
    assert gate["_history_end_date"](at) == expected


def test_history_end_date_fail_closed_for_naive_clock():
    gate = _functions("_history_end_date")
    gate.update(date=date, timedelta=timedelta,
                MARKET_CLOSE_HOUR=17, MARKET_CLOSE_MINUTE=0,
                BYMA_CALENDAR_AUDITED_YEAR=2026,
                _business_day=lambda _: True)
    assert gate["_history_end_date"](
        datetime.fromisoformat("2026-11-02T19:00:00")) is None


def _background_database(tmp_path):
    observer = tmp_path / "postclose-background.db"
    with sqlite3.connect(observer) as conn:
        conn.execute("""CREATE TABLE source_sync(
          source TEXT PRIMARY KEY, last_attempt_at TEXT)""")
        conn.execute("""CREATE TABLE api_health(
          component TEXT PRIMARY KEY, checked_at TEXT)""")
    return observer


@pytest.mark.parametrize("clock,expected", [
    ("17:00", False),
    ("18:14", False),
    ("18:15", True),
    ("18:16", True),
])
def test_background_ingest_due_only_after_eod_floor(tmp_path, clock, expected):
    database = _background_database(tmp_path)
    gate = _functions("_background_ingest_due")
    gate.update(_business_day=lambda day: day.weekday() < 5,
                MARKET_CLOSE_HOUR=17, MARKET_CLOSE_MINUTE=0)
    now = datetime.fromisoformat(f"2026-11-02T{clock}:00-03:00")
    assert gate["_background_ingest_due"](
        _History(database), now=now) is expected


def test_background_ingest_restart_same_day_does_not_reissue_attempt(tmp_path):
    database = _background_database(tmp_path)
    gate = _functions("_background_ingest_due")
    gate.update(_business_day=lambda day: day.weekday() < 5,
                MARKET_CLOSE_HOUR=17, MARKET_CLOSE_MINUTE=0)
    due = gate["_background_ingest_due"]
    assert due(_History(database), now=NOW)
    with sqlite3.connect(database) as conn:
        conn.execute("INSERT INTO api_health VALUES(?,?)",
                     ("PPI_BACKGROUND_INGEST", NOW.isoformat()))
    # Fresh store/observer after process restart still sees durable receipt.
    assert due(_History(database), now=NOW + timedelta(minutes=1)) is False
    with sqlite3.connect(database) as conn:
        conn.execute("INSERT INTO source_sync VALUES(?,?)",
                     ("PPI_PRODUCTION_HISTORY", NOW.isoformat()))
        conn.execute("DELETE FROM api_health")
    assert due(_History(database), now=NOW + timedelta(minutes=2)) is False
    assert due(_History(database), now=NOW + timedelta(days=1)) is True


def test_daily_catalog_still_runs_at_spot_close_without_early_history_get():
    gate = _functions("_history_end_date", "_history_daily_sync_due", "_daily_sync")
    calls = []
    gate.update(date=date, timedelta=timedelta,
                MARKET_CLOSE_HOUR=17, MARKET_CLOSE_MINUTE=0,
                BYMA_CALENDAR_AUDITED_YEAR=2026,
                _business_day=lambda day: day.weekday() < 5,
                _history_cutoff_repair_complete=lambda: True,
                _source_sync_due=lambda _store, source, _now: True,
                _download_catalog=lambda *_args: calls.append("CATALOG") or 1,
                _download_histories=lambda *_args: calls.append("HISTORY") or 1,
                _sync_state=lambda *_args: None)
    class _Store:
        def event(self, *_args):
            pass

    gate["_daily_sync"](object(), _Store(),
                        now=datetime.fromisoformat("2026-11-02T17:00:00-03:00"))
    assert calls == ["CATALOG"]


def test_daily_and_background_paths_share_one_durable_history_attempt(tmp_path):
    database = _background_database(tmp_path)
    gate = _functions("_history_end_date", "_source_sync_due",
                      "_history_daily_sync_due", "_background_ingest_due",
                      "_daily_sync")
    gate.update(date=date, timedelta=timedelta,
                BYMA_CALENDAR_AUDITED_YEAR=2026,
                _business_day=lambda day: day.weekday() < 5,
                _history_cutoff_repair_complete=lambda: True,
                _sync_state=lambda *_args: None)
    events = []

    class _Store(_History):
        def event(self, *args):
            events.append(args)

    store = _Store(database)
    requests = []
    clock = [datetime.fromisoformat("2026-11-02T17:00:00-03:00")]

    def _capture(source, kind):
        requests.append(kind)
        with store.connect() as conn:
            conn.execute("INSERT INTO source_sync VALUES(?,?)",
                         (source, clock[0].isoformat()))
        return 1

    gate.update(_download_catalog=lambda *_args: _capture(
                    "PPI_PRODUCTION_CATALOG", "CATALOG"),
                _download_histories=lambda *_args: _capture(
                    "PPI_PRODUCTION_HISTORY", "HISTORY"))
    sync = gate["_daily_sync"]
    background = gate["_background_ingest_due"]
    sync(object(), store, clock[0])
    assert requests == ["CATALOG"]
    clock[0] = datetime.fromisoformat("2026-11-02T18:16:00-03:00")
    sync(object(), store, clock[0])
    assert requests == ["CATALOG", "HISTORY"]
    assert background(_Store(database), now=clock[0]) is False
    clock[0] += timedelta(minutes=1)
    sync(object(), _Store(database), clock[0])
    assert requests == ["CATALOG", "HISTORY"]
