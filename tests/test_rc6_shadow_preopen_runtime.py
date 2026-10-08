"""Real SQLite contracts for the causal read-only SHADOW preopen bridge."""
from datetime import date, datetime, timedelta, timezone
from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
from time import monotonic

import pytest

from cu_history_store_v2_hf6 import Candle, append_many
from rc6_dynamic_universe.tradeability import anomaly_events, freeze_preopen, rank_tradeability
from rc6_shadow_runtime import preopen

AS_OF = "2026-10-05T13:15:00+00:00"
OPEN = "2026-10-05T13:30:00+00:00"
CUT = "2026-10-02T20:00:05+00:00"
A = {"ticker": "A", "instrument_type": "ACCIONES", "market": "BYMA", "currency": "ARS", "settlement": "A-24HS"}
B = {**A, "ticker": "B", "instrument_type": "CEDEARS"}
BOND = {**A, "ticker": "BOND", "instrument_type": "BONOS"}
CATALOG = [A, B, BOND]


def _sessions():
    sessions = []
    day = date(2026, 10, 2)
    while len(sessions) < 20:
        if preopen.calendar.es_dia_habil_operativo(day):
            sessions.append(day.isoformat())
        day -= timedelta(days=1)
    return sorted(sessions)


def _make_database(path, *, catalogue=CATALOG):
    with sqlite3.connect(path) as connection:
        connection.executescript("""
          CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER);
          INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0);
          CREATE TABLE financial_instrument_catalog(ticker TEXT,instrument_type TEXT,market TEXT,
            currency TEXT,settlement TEXT,status TEXT,capability TEXT);
          CREATE TABLE production_history(symbol TEXT,instrument_type TEXT,settlement TEXT,
            date_from TEXT,date_to TEXT,downloaded_at TEXT,row_count INTEGER,payload_json TEXT);
          CREATE TABLE historical_raw_archive(id INTEGER PRIMARY KEY,origin TEXT,row_key TEXT,
            body_hash TEXT,recorded_at TEXT,quality TEXT,body_json TEXT);
          CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY,source TEXT,observed_at TEXT,
            symbol TEXT,asset_class TEXT,market TEXT,currency TEXT,settlement TEXT,
            book_at TEXT,bid TEXT,ask TEXT,bid_size TEXT,ask_size TEXT,contract_json TEXT);
          CREATE TABLE ppi_intraday_points(symbol TEXT,asset_class TEXT,market TEXT,currency TEXT,
            settlement TEXT,event_at TEXT,price TEXT,volume TEXT,first_received_at TEXT,
            last_verified_at TEXT,source TEXT);
        """)
        for record in catalogue:
            connection.execute("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,'AVAILABLE','READY_PAPER_SPOT')",
                               tuple(record[name] for name in preopen.IDENTITY_FIELDS))
    return path


def _history(path, *, metadata=True, volume=100, available=None, source="PPI_PRODUCTION_HISTORY", adjusted=False):
    class FixtureStore:
        @contextmanager
        def connect(self):
            connection=sqlite3.connect(path)
            connection.row_factory=sqlite3.Row
            try:yield connection;connection.commit()
            except BaseException:connection.rollback();raise
            finally:connection.close()
    records=[]
    for day in _sessions():
        data={"source_at":day+"T20:00:00+00:00","currency":"ARS","volume_unit":"SHARES"} if metadata else {}
        records.append(Candle('A','ACCIONES','BYMA','A-24HS',day,100,101,99,100,
            volume,source,adjusted,available or day+'T20:00:02+00:00',data,
            currency='ARS',price_basis='UNKNOWN_ADJUSTED' if adjusted else 'RAW',
            volume_kind='QUANTITY' if metadata else 'UNKNOWN',
            provider_at=day+'T20:00:00+00:00' if metadata else None))
    append_many(FixtureStore(),records)


def _books(path, *, explicit_units=False, available=None):
    with sqlite3.connect(path) as connection:
        for seconds in (50, 52, 54):
            contract = {"quantity_unit": "SHARES"} if explicit_units else {"family": "ACCIONES"}
            connection.execute("""INSERT INTO market_snapshots VALUES(NULL,'PPI_CURRENT_BOOK',?,
              'A','ACCIONES','BYMA','ARS','A-24HS',?,'99.9','100.1','20','30',?)""",
              (available or f"2026-10-02T19:59:{seconds+1}+00:00", f"2026-10-02T19:59:{seconds}+00:00", json.dumps(contract)))


def _points(path, *, revised_after_cut=False):
    with sqlite3.connect(path) as connection:
        for index, day in enumerate(_sessions()[-5:]):
            for minute in (25, 30):
                event = datetime.fromisoformat(day+"T13:30:00+00:00")+timedelta(minutes=minute)
                received = event+timedelta(seconds=1)
                connection.execute("INSERT INTO ppi_intraday_points VALUES('A','ACCIONES','BYMA','ARS','A-24HS',?,?,?,?,?,'PPI_MARKETDATA_INTRADAY')",
                  (event.isoformat(), str(100 if minute == 25 else 100+index*.1), "900000", received.isoformat(), AS_OF if revised_after_cut else received.isoformat()))


def _read(path, history=None, **kwargs):
    return preopen.build_preopen_inputs(path, history, as_of=AS_OF, session_open=OPEN, cutoff=CUT, **kwargs)


def _rank(inputs):
    return rank_tradeability(CATALOG, inputs["history"], inputs["preopen_observations"], cutoff=CUT, sessions=inputs["sessions"])


def _payload(*, unit="SHARES", currency="ARS"):
    return [{"date": day+"T20:00:00+00:00", "openingPrice": 100, "max": 101, "min": 99,
             "price": 100, "volume": 100, "volume_unit": unit, "turnover": 10000,
             "turnover_currency": currency, "trades": 10} for day in _sessions()]


def _production(path, *, payload=None, available=CUT):
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO production_history VALUES('A','ACCIONES','A-24HS',?,?,?,20,?)",
                           (_sessions()[0], _sessions()[-1], available, json.dumps(payload if payload is not None else _payload())))


def test_history_store_real_versions_feed_frozen_rank_and_keep_full_catalogue(tmp_path):
    source = _make_database(tmp_path/"trading.db")
    history = tmp_path/"history.db"
    _history(history)
    _books(source, explicit_units=True)
    inputs = _read(source, history)
    assert len(inputs["history"]) == 20
    assert inputs["quality"]["ready_catalog_count"] == 3
    assert inputs["quality"]["source_database_effect"] == "READ_ONLY"
    report = _rank(inputs)
    assert [row["ticker"] for row in report["rows"]] == ["A", "B", "BOND"]
    assert report["rows"][0]["components"]["activity20"]["value"] == 1
    assert report["rows"][0]["components"]["median_volume"]["unit"] == "SHARES"
    assert report["rows"][0]["components"]["range_bps"]["value"] == 200
    assert report["rows"][0]["tradeable"]
    assert report["rows"][1]["components"]["activity20"]["status"] == "NO_VERIFICADO"
    frozen = freeze_preopen(report, frozen_at=AS_OF, session_open=OPEN, capacity_fingerprint="observed-capacity")
    assert len(frozen.payload["rows"]) == len(CATALOG)
    assert frozen.payload["real_orders_sent"] == 0


def test_eventless_history_store_versions_never_invent_event_or_volume_unit(tmp_path):
    source = _make_database(tmp_path/"trading.db")
    history = tmp_path/"history.db"
    _history(history, metadata=False)
    inputs = _read(source, history)
    assert inputs["history"] == []
    assert inputs["quality"]["rejected_inputs"]["HISTORY_SOURCE_EVENT_TIME_NO_VERIFICADO"] == 20
    assert _rank(inputs)["rows"][0]["components"]["median_volume"]["value"] is None


def test_append_only_raw_history_survives_later_mutable_production_revision(tmp_path):
    source = _make_database(tmp_path/"trading.db")
    wrapper = {"symbol": "A", "asset_class": "ACCIONES", "settlement": "A-24HS", "metadata": A,
               "payload_json": json.dumps(_payload())}
    with sqlite3.connect(source) as connection:
        connection.execute("INSERT INTO historical_raw_archive VALUES(1,'PPI_HISTORY','row','hash',?,'VALID_PAYLOAD',?)",
                           (CUT, json.dumps(wrapper)))
    _production(source, payload=[dict(row, volume=999999) for row in _payload()], available=AS_OF)
    inputs = _read(source)
    assert len(inputs["history"]) == 20
    assert all(row["volume"] == 100 for row in inputs["history"])
    assert inputs["quality"]["source_rows_read"]["historical_raw_archive"] == 1
    assert inputs["quality"]["source_rows_read"]["production_history"] == 0


def test_history_versions_reconstruct_causal_source_authority_at_cutoff(tmp_path):
    source = _make_database(tmp_path/"trading.db")
    history = tmp_path/"history.db"
    _history(history)
    _history(history, volume=999, source="DATA912", available=CUT)
    # The correction is after the query cut and already in the real past;
    # the authoritative writer must not ingest a future known_at fixture.
    _history(history, volume=999999, available=(datetime.fromisoformat(CUT)+timedelta(seconds=1)).isoformat())
    inputs = _read(source, history)
    assert all(row["volume"] == 100 for row in inputs["history"])
    assert all(row["source"] == "PPI_PRODUCTION_HISTORY" for row in inputs["history"])


def test_daily_raw_units_currency_and_timestamp_absence_stay_unknown(tmp_path):
    source = _make_database(tmp_path/"trading.db")
    _production(source, payload=_payload(unit="NOMINAL", currency="USD"))
    _books(source)
    inputs = _read(source)
    row = _rank(inputs)["rows"][0]
    assert row["components"]["median_volume"]["value"] is None
    assert row["components"]["median_turnover"]["value"] is None
    assert row["components"]["depth_paper_multiple"]["value"] is None
    assert inputs["quality"]["status"] == "NO_VERIFICADO"
    assert "VOLUME_UNIT_NO_VERIFICADO" in inputs["quality"]["rejected_inputs"]
    with sqlite3.connect(source) as connection:
        connection.execute("DELETE FROM production_history")
    _production(source, payload=[dict(row, date=row["date"][:10]) for row in _payload()])
    assert _read(source)["history"] == []


def test_previous_intraday_prices_drive_real_profile_without_relabeling_interval_volume(tmp_path):
    source = _make_database(tmp_path/"trading.db")
    _points(source)
    inputs = _read(source)
    assert len(inputs["intraday_history"]) == 10
    assert {row["minute_of_session"] for row in inputs["intraday_history"]} == {25, 30}
    assert all(row["volume_unit"] == "NO_VERIFICADO" and "cumulative_volume" not in row for row in inputs["intraday_history"])
    current = [{**A, "session": "2026-10-05", "minute_of_session": minute, "price": price,
                "observed_at": f"2026-10-05T13:{30+minute}:00+00:00" if minute < 30 else "2026-10-05T14:00:00+00:00",
                "published_at": f"2026-10-05T13:{30+minute}:01+00:00" if minute < 30 else "2026-10-05T14:00:01+00:00",
                "source": "LIVE_SHADOW", "usable": True} for minute, price in ((25, 100), (30, 110))]
    event = anomaly_events(inputs["intraday_history"], current, as_of="2026-10-05T14:00:02+00:00")["events"][0]
    assert event["features"]["profile_sessions"] == 5
    assert event["features"]["normalized_price_shock"] > 3
    assert event["features"]["rvol"] is None
    assert event["promotion_candidate"]
    assert event["entry_authority"] is False


def test_mutable_intraday_revision_verified_after_cutoff_cannot_reconstruct_past_value(tmp_path):
    source = _make_database(tmp_path/"trading.db")
    _points(source, revised_after_cut=True)
    assert _read(source)["intraday_history"] == []


def test_request_identity_ambiguity_never_assigns_history_to_currency_sibling(tmp_path):
    source = _make_database(tmp_path/"trading.db", catalogue=[A, {**A, "currency": "USD"}, BOND])
    _production(source)
    inputs = _read(source)
    assert inputs["history"] == []
    assert inputs["quality"]["ready_catalog_count"] == 3
    assert inputs["quality"]["rejected_inputs"]["SOURCE_IDENTITY_NO_VERIFICADO"] == 1


def test_nonready_currency_sibling_still_makes_provider_request_ambiguous(tmp_path):
    source = _make_database(tmp_path/"trading.db", catalogue=[A, {**A, "currency": "USD"}, BOND])
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE financial_instrument_catalog SET capability='DATA_ONLY' WHERE currency='USD'")
    _production(source)
    inputs = _read(source)
    assert inputs["history"] == []
    assert inputs["quality"]["ready_catalog_count"] == 2
    assert inputs["quality"]["rejected_inputs"]["SOURCE_IDENTITY_NO_VERIFICADO"] == 1


def test_explicit_later_publication_cannot_be_backdated_by_earlier_receipt(tmp_path):
    source = _make_database(tmp_path/"trading.db")
    _production(source, payload=[dict(row, published_at=AS_OF) for row in _payload()])
    inputs = _read(source)
    assert inputs["history"] == []
    assert inputs["quality"]["rejected_inputs"]["FUTURE_INFORMATION_REJECTED"] == 20


def test_oversized_payload_is_never_loaded_or_silently_used(tmp_path, monkeypatch):
    source = _make_database(tmp_path/"trading.db")
    _production(source)
    monkeypatch.setattr(preopen, "MAX_PAYLOAD_BYTES", 100)
    inputs = _read(source)
    assert inputs["history"] == []
    assert "production_history_oversized_payload" in inputs["quality"]["truncated_sources"]
    assert inputs["quality"]["payload_bytes_read"] == 0


def test_calendar_uses_audited_holidays_and_does_not_guess_unaudited_year(tmp_path):
    source = _make_database(tmp_path/"trading.db")
    inputs = preopen.build_preopen_inputs(source, as_of="2026-10-13T13:15:00Z", session_open="2026-10-13T13:30:00Z", cutoff="2026-10-12T23:00:00Z")
    assert inputs["sessions"][-1] == "2026-10-09"
    assert "2026-10-12" not in inputs["sessions"]
    inputs = preopen.build_preopen_inputs(source, as_of="2027-01-04T13:15:00Z", session_open="2027-01-04T13:30:00Z", cutoff="2027-01-03T23:00:00Z")
    assert inputs["sessions"] == []
    assert inputs["quality"]["calendar_status"] == "AUDITED_CALENDAR_NO_VERIFICADO"


def test_reader_does_not_negotiate_wal_or_take_writer_lock(tmp_path):
    source = _make_database(tmp_path/"trading.db")
    _production(source)
    before = source.read_bytes()
    with sqlite3.connect(source, timeout=.01) as writer:
        assert writer.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        writer.execute("BEGIN IMMEDIATE")
        # A verified byte copy can read a committed image under an unused
        # lock. Dirty rollback-journal recovery belongs exclusively to a copy.
        started = monotonic()
        inputs = _read(source)
        assert monotonic()-started < .5
        assert len(inputs["history"]) == 20
        writer.rollback()
        assert writer.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert source.read_bytes() == before
    assert not source.with_name(source.name+"-wal").exists()
    assert not source.with_name(source.name+"-shm").exists()


def test_authorizer_forbids_source_mutations_and_closes_read_transaction(tmp_path, monkeypatch):
    source = _make_database(tmp_path/"trading.db")
    _production(source)
    original = sqlite3.connect
    forbidden = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE, sqlite3.SQLITE_CREATE_TABLE,
                 sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_DROP_INDEX}
    observed = []
    def guarded(path, *args, **kwargs):
        assert str(path).endswith("?mode=ro")
        assert kwargs["uri"] is True
        connection = original(path, *args, **kwargs)
        def authorize(action, first, second, database, origin):
            if action in forbidden or (action == sqlite3.SQLITE_PRAGMA and first == "journal_mode" and second is not None):
                observed.append((action, first, second))
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        connection.set_authorizer(authorize)
        return connection
    monkeypatch.setattr(preopen.sqlite3, "connect", guarded)
    assert len(_read(source)["history"]) == 20
    assert observed == []
    # An unclosed read transaction would block this DELETE-journal writer.
    with original(source, timeout=.01) as writer:
        writer.execute("UPDATE observer_state SET real_orders_sent=0")


def test_exclusive_writer_contention_is_bounded_and_explicit(tmp_path):
    source = _make_database(tmp_path/"trading.db")
    with sqlite3.connect(source) as writer:
        writer.execute("BEGIN EXCLUSIVE")
        writer.execute("UPDATE observer_state SET real_orders_sent=1")
        started = monotonic()
        inputs = _read(source)
        assert monotonic()-started < .5
        assert inputs["history"] == []
        assert inputs["quality"]["unavailable_sources"] == ["trading:SOURCE_SNAPSHOT_BUSY"]
        writer.rollback()


def test_missing_sources_are_not_created_and_safety_failures_propagate(tmp_path):
    missing = tmp_path/"missing.db"
    inputs = _read(missing)
    assert not missing.exists()
    assert inputs["quality"]["unavailable_sources"] == ["trading:FileNotFoundError"]
    source = _make_database(tmp_path/"trading.db")
    inputs = _read(source, missing)
    assert inputs["quality"]["unavailable_sources"] == ["history:FileNotFoundError"]
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE observer_state SET real_orders_sent=1")
    with pytest.raises(ValueError, match="PAPER_SAFETY_REQUIRED"):
        _read(source)


def test_full_catalogue_truncation_and_invalid_cuts_fail_closed(tmp_path):
    source = _make_database(tmp_path/"trading.db")
    with pytest.raises(ValueError, match="CATALOG_READ_TRUNCATED"):
        _read(source, row_limit=1)
    with pytest.raises(ValueError, match="PREOPEN_CUT_READ_OPEN_ORDER_REQUIRED"):
        preopen.build_preopen_inputs(source, as_of=OPEN, session_open=OPEN, cutoff=CUT)
    with pytest.raises(ValueError, match="AWARE_TIMESTAMP_REQUIRED"):
        preopen.build_preopen_inputs(source, as_of="2026-10-05T13:15:00", session_open=OPEN, cutoff=CUT)


def test_daily_publication_before_close_and_malformed_intraday_clocks_are_rejected(tmp_path):
    source = _make_database(tmp_path/"trading.db")
    _production(source, available="2026-10-02T19:00:00+00:00")
    _points(source)
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE ppi_intraday_points SET first_received_at=?", (CUT,))
    inputs = _read(source)
    assert len(inputs["history"]) == 19
    assert inputs["intraday_history"] == []
    assert inputs["quality"]["rejected_inputs"]["INTRADAY_AVAILABILITY_CLOCK_ORDER_INVALID"] == 10
    assert _rank(inputs)["rows"][0]["components"]["activity20"]["value"] is None


def _worker_store(tmp_path):
    from tests.test_rc6_shadow_runtime_wiring import make_store
    store, _ = make_store(tmp_path, count=1)
    with closing(store.connect()) as connection, connection:
        connection.execute("UPDATE financial_instrument_catalog SET ticker='A'")
    return store


def _frozen_wal_history(tmp_path, *, adjusted=False):
    original = tmp_path/"builder.sqlite"
    writer = sqlite3.connect(original)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("CREATE TABLE fixture_builder_marker(value TEXT)")
    writer.commit()
    try:
        _history(original)
        if adjusted:
            _history(original, volume=9999, source="DATA912", adjusted=True)
        folder = tmp_path/"frozen-history"
        folder.mkdir()
        source = folder/"history.sqlite"
        source.write_bytes(original.read_bytes())
        Path(str(source)+"-wal").write_bytes(Path(str(original)+"-wal").read_bytes())
    finally:
        writer.close()
    return folder, source


def test_NEW_U24_preopen_tick_WAL_without_SHM_preserves_source_inventory_and_all_stats(tmp_path, monkeypatch):
    from tests.test_rc6_history_snapshot_copy import inventory
    from tests.test_rc6_shadow_runtime_wiring import snapshot
    from rc6_shadow_runtime.worker import ShadowRuntime
    store = _worker_store(tmp_path)
    folder, history = _frozen_wal_history(tmp_path)
    before = inventory(folder)
    original = sqlite3.connect
    def guarded(path, *args, **kwargs):
        assert str(history) not in str(path), "Historical source must never be opened by SQLite"
        return original(path, *args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", guarded)
    worker = ShadowRuntime(store.path, history_database=history, evidence_root=tmp_path/"shadow", source_roots=[])
    report = worker.tick(AS_OF)
    _, frozen = snapshot(worker)
    # The real worker cut is 20:00:00; Friday's receipt at 20:00:02 is later.
    assert frozen["quality"]["accepted_rows"]["history"] == 19
    assert frozen["quality"]["cutoff"] == "2026-10-02T20:00:00+00:00"
    assert frozen["quality"]["unavailable_sources"] == []
    assert frozen["quality"]["source_snapshot_method"] == "VERIFIED_MAIN_WAL_PRIVATE_COPY_SOURCE_SQLITE_NEVER_OPENED"
    assert report["provider_requests"] == report["real_orders_sent"] == 0
    assert inventory(folder) == before
    assert not Path(str(history)+"-shm").exists()


def test_NEW_AUD06_preopen_tick_adjusted_Data912_cannot_replace_RAW_PPI_profile(tmp_path):
    from tests.test_rc6_history_snapshot_copy import inventory
    from tests.test_rc6_shadow_runtime_wiring import snapshot
    from rc6_shadow_runtime.worker import ShadowRuntime
    store = _worker_store(tmp_path)
    folder, history = _frozen_wal_history(tmp_path, adjusted=True)
    before = inventory(folder)
    worker = ShadowRuntime(store.path, history_database=history, evidence_root=tmp_path/"shadow", source_roots=[])
    report = worker.tick(AS_OF)
    _, frozen = snapshot(worker)
    for strategy in frozen["frozen"].values():
        row = next(row for row in strategy["payload"]["rows"] if row["ticker"] == "A")
        assert row["components"]["median_volume"]["value"] == 100
        assert row["components"]["median_volume"]["unit"] == "SHARES"
    assert frozen["quality"]["rejected_inputs"]["HISTORY_PRICE_BASIS_INCOMPARABLE"] == 19
    assert frozen["quality"]["accepted_rows"]["history"] == 19
    assert report["provider_requests"] == report["real_orders_sent"] == 0
    assert inventory(folder) == before


def test_NEW_preopen_latest_oversized_metadata_fails_closed_without_resurrecting_old_revision(tmp_path):
    source = _make_database(tmp_path/"trading.db")
    history = tmp_path/"history.db"
    _history(history)
    with sqlite3.connect(history) as connection:
        connection.row_factory = sqlite3.Row
        first = connection.execute("SELECT * FROM history_versions_v2 ORDER BY date LIMIT 1").fetchone()
    class FixtureStore:
        @contextmanager
        def connect(self):
            connection = sqlite3.connect(history)
            connection.row_factory = sqlite3.Row
            try:
                yield connection
                connection.commit()
            finally:
                connection.close()
    value = Candle('A', 'ACCIONES', 'BYMA', 'A-24HS', first['date'], 100, 101, 99, 100,
        101, 'PPI_PRODUCTION_HISTORY', False, CUT,
        {"currency": "ARS", "volume_unit": "SHARES", "private": "x"*(preopen.MAX_NATIVE_JSON_BYTES+1)},
        currency='ARS', provider_at=first['provider_at'], volume_kind='QUANTITY')
    append_many(FixtureStore(), [value])
    inputs = _read(source, history)
    assert inputs["history"] == []
    assert "history_versions_v2_oversized_payload" in inputs["quality"]["truncated_sources"]
    assert inputs["quality"]["status"] == "NO_VERIFICADO"
    assert inputs["quality"]["payload_bytes_read"] < preopen.MAX_TOTAL_PAYLOAD_BYTES


def test_NEW_preopen_context_deadline_never_publishes_partially_read_history(tmp_path, monkeypatch):
    from rc6_audit_evidence.sqlite_snapshot import SnapshotError
    source = _make_database(tmp_path/"trading.db")
    _production(source)
    original = preopen._source
    @contextmanager
    def expired(path, deadline):
        with original(path, deadline) as connection:
            yield connection
            raise SnapshotError("TIME_BUDGET_EXHAUSTED")
    monkeypatch.setattr(preopen, "_source", expired)
    inputs = _read(source)
    assert inputs["history"] == inputs["preopen_observations"] == inputs["intraday_history"] == []
    assert inputs["quality"]["unavailable_sources"] == ["trading:TIME_BUDGET_EXHAUSTED"]


def test_NEW_preopen_native_currency_provider_clock_and_unknown_quantity_kind_are_explicit(tmp_path):
    source = _make_database(tmp_path/"trading.db", catalogue=[A, {**A, "currency": "USD"}])
    history = tmp_path/"history.db"
    _history(history)
    with sqlite3.connect(history) as connection:
        connection.execute("UPDATE history_versions_v2 SET metadata_json='{}',volume_kind='UNKNOWN'")
    inputs = _read(source, history)
    assert len(inputs["history"]) == 20
    assert {row["currency"] for row in inputs["history"]} == {"ARS"}
    assert {row["volume_unit"] for row in inputs["history"]} == {"NO_VERIFICADO"}
    assert {row["source_clock_meaning"] for row in inputs["history"]} == {"EXPLICIT_PROVIDER_EVENT"}
    assert inputs["quality"]["status"] == "NO_VERIFICADO"


def test_NEW_preopen_microsecond_known_cut_and_currency_conflict_are_not_backdated(tmp_path):
    source = _make_database(tmp_path/"trading.db")
    history = tmp_path/"history.db"
    _history(history, available=(datetime.fromisoformat(CUT)+timedelta(microseconds=1)).isoformat())
    assert _read(source, history)["history"] == []
    with sqlite3.connect(history) as connection:
        connection.execute("UPDATE history_versions_v2 SET observed_at=?,version_known_at=?,metadata_json=?",
                           (CUT, CUT, json.dumps({"currency": "USD", "volume_unit": "SHARES"})))
    inputs = _read(source, history)
    assert inputs["history"] == []
    assert inputs["quality"]["rejected_inputs"]["SOURCE_IDENTITY_NO_VERIFICADO"] == 20


def test_NEW_preopen_python_processing_obeys_deadline_and_retains_cleanup_reserve(tmp_path, monkeypatch):
    source = _make_database(tmp_path/"trading.db")
    history = tmp_path/"history.db"
    _history(history)
    clock = [monotonic()]
    original = preopen._clock_pair
    visited = []
    def costly_validation(*args, **kwargs):
        # Simulate expensive valid native rows with a controlled monotonic
        # clock; the SQL progress handler has already finished fetching rows.
        clock[0] += .25
        visited.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(preopen, "monotonic", lambda: clock[0])
    monkeypatch.setattr(preopen, "_clock_pair", costly_validation)
    inputs = _read(source, history)
    assert 1 <= len(visited) < 20
    assert inputs["history"] == []
    assert inputs["quality"]["unavailable_sources"] == ["history:TIME_BUDGET_EXHAUSTED"]
    assert inputs["quality"]["source_snapshot_limits"]["processing_seconds"] == 1.9
    assert inputs["quality"]["source_snapshot_limits"]["cleanup_reserve_seconds"] == .1


def test_NEW_preopen_unit_only_correction_revokes_quantity_at_known_cut_and_preserves_source(tmp_path, monkeypatch, record_property):
    from tests.test_rc6_history_convergence import Store
    from tests.test_rc6_history_snapshot_copy import inventory
    source = _make_database(tmp_path/"trading.db", catalogue=[A])
    folder = tmp_path/"historical-source"
    folder.mkdir()
    historical = folder/"history.sqlite"
    for unit,known in (("SHARES","2026-10-01T16:00:00Z"),("UNKNOWN","2026-10-01T17:00:00Z")):
        append_many(Store(historical),[Candle("A","ACCIONES","BYMA","A-24HS","2026-09-30",
            100,101,99,100,10,"PPI_API",False,known,{"volume_unit":unit},currency="ARS",
            volume_kind="QUANTITY",provider_at="2026-09-30T20:00:00Z")])
    before = inventory(folder)
    original = sqlite3.connect
    def guarded(path,*args,**kwargs):
        assert str(historical) not in str(path), "SQLite must never open the historical source"
        return original(path,*args,**kwargs)
    monkeypatch.setattr(sqlite3,"connect",guarded)
    old = preopen.build_preopen_inputs(source,historical,as_of=AS_OF,session_open=OPEN,cutoff="2026-10-01T16:30:00Z")
    current = preopen.build_preopen_inputs(source,historical,as_of=AS_OF,session_open=OPEN,cutoff="2026-10-01T17:30:00Z")
    assert len(old["history"])==len(current["history"])==1
    assert old["history"][0]["volume_unit"]=="SHARES"
    assert current["history"][0]["volume_unit"]=="UNKNOWN"
    assert current["history"][0]["version_known_at"]=="2026-10-01T17:00:00.000000+00:00"
    old_rank = rank_tradeability([A],old["history"],[],cutoff="2026-10-01T16:30:00Z",sessions=old["sessions"])
    new_rank = rank_tradeability([A],current["history"],[],cutoff="2026-10-01T17:30:00Z",sessions=current["sessions"])
    assert old_rank["rows"][0]["components"]["median_volume"]["value"]==10
    assert new_rank["rows"][0]["components"]["median_volume"]["value"] is None
    assert new_rank["rows"][0]["components"]["median_volume"]["status"]=="NO_VERIFICADO"
    assert current["quality"]["rejected_inputs"]["VOLUME_UNIT_NO_VERIFICADO"]==1
    after = inventory(folder)
    assert after==before
    record_property("semantic_unit_source_before",str(before))
    record_property("semantic_unit_source_after",str(after))
    record_property("semantic_unit_cuts",str({"old":"2026-10-01T16:30:00Z","current":"2026-10-01T17:30:00Z",
        "old_median_volume":old_rank["rows"][0]["components"]["median_volume"],
        "new_median_volume":new_rank["rows"][0]["components"]["median_volume"]}))
