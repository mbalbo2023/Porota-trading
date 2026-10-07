"""Real SQLite decoder controls against the frozen pre-fix native Row reader.

The reference below is the Source2083df31 read_runtime body with only its
function name changed. It is a comparison oracle, never a native gate entry.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
import hashlib
import os
import sqlite3
import stat
from types import SimpleNamespace

import pytest

from rc6_dynamic_universe import runtime
from rc6_dynamic_universe.common import stamp
from rc6_shadow_runtime.source_reads import source_connection
from tests.rc6_external_disk_fixture import external_disk_fixture


def _legacy_read_runtime(database, *, as_of, row_limit=20000, query_budget_seconds=0.5):
    """No schema/init/writer, history ingestion, provider calls or broker creation.

    Current revisions require last_verified_at at/before cutoff; older values
    cannot be reconstructed from mutable rows. Partial coverage is explicit.
    """
    at = stamp(as_of)
    if not 1 <= row_limit <= 50000 or not 0 < query_budget_seconds <= 2:
        raise ValueError("INVALID_READ_BUDGET")
    deadline = monotonic()+query_budget_seconds
    with source_connection(database, deadline=deadline) as (connection, _):
        connection.execute("PRAGMA query_only=ON")
        connection.set_progress_handler(lambda: int(monotonic() > deadline), 1000)
        state = connection.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if not state or state["mode"] != "PRODUCTION_PAPER" or state["real_orders_sent"] != 0:
            raise ValueError("PAPER_SAFETY_REQUIRED")
        catalog = [dict(r) for r in connection.execute("""SELECT ticker,instrument_type,market,
            currency,settlement,status,capability FROM financial_instrument_catalog
            WHERE status='AVAILABLE' AND capability LIKE 'READY_PAPER%'
            ORDER BY instrument_type,market,currency,ticker,settlement LIMIT ?""", (row_limit+1,))]
        if len(catalog) > row_limit:
            raise ValueError("CATALOG_READ_TRUNCATED")
        full_catalog = [dict(r) for r in connection.execute("""SELECT ticker,instrument_type,market,
            currency,settlement,status,capability FROM financial_instrument_catalog
            ORDER BY instrument_type,market,currency,ticker,settlement LIMIT ?""", (row_limit+1,))]
        if len(full_catalog) > row_limit:
            raise ValueError("FULL_CATALOG_READ_TRUNCATED")
        opened = [tuple(r) for r in connection.execute("""SELECT symbol,asset_class,market,currency,settlement
            FROM paper_positions WHERE status='OPEN' ORDER BY paper_id LIMIT ?""", (row_limit+1,))]
        if len(opened) > row_limit:
            raise ValueError("OPEN_POSITION_READ_TRUNCATED")
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        observations = []
        if "ppi_intraday_points" in tables:
            contracts = {}
            if "ppi_intraday_contract_state" in tables:
                for r in connection.execute("SELECT * FROM ppi_intraday_contract_state LIMIT ?", (row_limit+1,)):
                    k = tuple(r[x] for x in ("symbol", "asset_class", "market", "currency", "settlement"))
                    try:
                        confirmed = (r["state"] == "CONFIRMED_INTERVAL_VOLUME" and
                            0 <= (at-stamp(r["last_source_at"])).total_seconds() <= 120 and
                            0 <= (at-stamp(r["checked_at"])).total_seconds() <= 120 and
                            stamp(r["last_source_at"]) <= stamp(r["checked_at"]))
                    except (ValueError, TypeError):
                        confirmed = False
                    contracts[k] = confirmed
            rows = list(connection.execute("""SELECT symbol,asset_class,market,currency,settlement,
                event_at,first_received_at,last_verified_at,price,volume,source FROM ppi_intraday_points
                WHERE julianday(event_at)>=julianday(?) AND julianday(event_at)<=julianday(?)
                  AND julianday(first_received_at)<=julianday(?)
                  AND julianday(last_verified_at)<=julianday(?)
                ORDER BY julianday(event_at) DESC LIMIT ?""", (at.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
                                                    at.isoformat(), at.isoformat(), at.isoformat(), row_limit+1)))
            for r in rows[:row_limit]:
                key = tuple(r[k] for k in ("symbol", "asset_class", "market", "currency", "settlement"))
                observations.append({"identity": key, "source_at": r["event_at"],
                    "received_at": r["last_verified_at"], "source": r["source"], "useful": True,
                    "endpoint": "intraday", "intraday_confirmed": contracts.get(key, False),
                    "fields": {"price": r["price"]},
                    "volume_semantics": "INTERVAL_VOLUME" if contracts.get(key, False) else "NO_VERIFICADO",
                    "volume_unit": "NO_VERIFICADO", "entry_authority": False})
            truncated = len(rows) > row_limit
        else:
            truncated = False
        if "market_snapshots" in tables:
            rows = list(connection.execute("""SELECT symbol,asset_class,market,currency,settlement,
                last,trade_at,book_at,observed_at,bid,ask,bid_size,ask_size,last_kind FROM market_snapshots
                WHERE julianday(observed_at)<=julianday(?) AND julianday(observed_at)>=julianday(?)
                ORDER BY id DESC LIMIT ?""", (at.isoformat(), at.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(), row_limit+1)))
            for r in rows[:row_limit]:
                if not r["trade_at"] or not r["book_at"]:
                    continue
                try:
                    current_fresh = 0 <= (at-stamp(r["trade_at"])).total_seconds() <= 120
                    book_fresh = (0 <= (at-stamp(r["book_at"])).total_seconds() <= 120 and
                        stamp(r["book_at"]) <= stamp(r["observed_at"]) and
                        0 < float(r["bid"]) <= float(r["ask"]) and
                        min(float(r["bid_size"]), float(r["ask_size"])) > 0)
                except (ValueError, TypeError):
                    continue
                key = tuple(r[k] for k in ("symbol", "asset_class", "market", "currency", "settlement"))
                observations.append({"identity": key, "source_at": r["trade_at"], "received_at": r["observed_at"],
                    "source": "PPI_MARKETDATA_CURRENT", "endpoint": "current", "useful": current_fresh,
                    "book_at": r["book_at"], "book_useful": book_fresh,
                    "is_trade": r["last_kind"] == "TRADE",
                    "fields": {"price": r["last"], **({"spread_bps":
                        (float(r["ask"])/float(r["bid"])-1)*10000} if book_fresh else {})},
                    "entry_authority": False})
            truncated |= len(rows) > row_limit
        return {"catalog": catalog, "full_catalog": full_catalog, "opened": opened, "observations": observations,
                "catalog_view": "ALL_READY_PAPER_IDENTITIES; financial catalogue unchanged",
                "observation_read_truncated": truncated,
                "safety": {"mode": "PRODUCTION_PAPER", "real_orders_sent": 0,
                           "real_routes": "NOT_CALLED"}, "source_database_effect": "READ_ONLY"}


def _source_inventory(database):
    result = {}
    for suffix in ("", "-wal", "-shm", "-journal"):
        path = Path(str(database)+suffix)
        try:
            before = path.lstat()
        except FileNotFoundError:
            continue
        descriptor = os.open(path, os.O_RDONLY | os.O_NOATIME | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            hashed = hashlib.sha256()
            while block := os.read(descriptor, 65536):
                hashed.update(block)
            assert os.fstat(descriptor) == before == path.lstat()
        finally:
            os.close(descriptor)
        result[suffix] = (before.st_dev, before.st_ino, before.st_uid, before.st_gid,
            before.st_mode, before.st_nlink, before.st_size, before.st_atime_ns,
            before.st_mtime_ns, before.st_ctime_ns, before.st_blocks, hashed.hexdigest())
    return result


def _clock_cases(at):
    return [
        ("FRESH", "CONFIRMED_INTERVAL_VOLUME", at.isoformat(), at.isoformat(), True),
        ("BOUND", "CONFIRMED_INTERVAL_VOLUME", (at-timedelta(seconds=120)).isoformat(), at.isoformat(), True),
        ("SOURCE_FUTURE", "CONFIRMED_INTERVAL_VOLUME", (at+timedelta(seconds=1)).isoformat(), "invalid-unvisited", False),
        ("SOURCE_STALE", "CONFIRMED_INTERVAL_VOLUME", (at-timedelta(seconds=121)).isoformat(), "invalid-unvisited", False),
        ("CHECKED_FUTURE", "CONFIRMED_INTERVAL_VOLUME", at.isoformat(), (at+timedelta(seconds=1)).isoformat(), False),
        ("CHECKED_STALE", "CONFIRMED_INTERVAL_VOLUME", at.isoformat(), (at-timedelta(seconds=121)).isoformat(), False),
        ("INVERTED", "CONFIRMED_INTERVAL_VOLUME", at.isoformat(), (at-timedelta(seconds=1)).isoformat(), False),
        ("BAD_SOURCE", "CONFIRMED_INTERVAL_VOLUME", "not-a-clock", at.isoformat(), False),
        ("BAD_CHECKED", "CONFIRMED_INTERVAL_VOLUME", at.isoformat(), "invalid-visited", False),
        ("NAIVE_SOURCE", "CONFIRMED_INTERVAL_VOLUME", at.replace(tzinfo=None).isoformat(), at.isoformat(), False),
        ("UNCONFIRMED", "UNKNOWN", "invalid-unvisited", "invalid-unvisited", False),
        ("OFFSET", "CONFIRMED_INTERVAL_VOLUME", at.astimezone(timezone(timedelta(hours=-3))).isoformat(),
         at.isoformat().replace("+00:00", "Z"), True),
        ("NULL_SOURCE", "CONFIRMED_INTERVAL_VOLUME", None, at.isoformat(), False),
    ]


@pytest.fixture
def scalar_source():
    with external_disk_fixture(prefix=".rc6-runtime-row-scalar-") as directory:
        database = Path(directory)/"source.db"
        at = datetime(2026, 10, 5, 13, 35, tzinfo=timezone.utc)
        connection = sqlite3.connect(database)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript("""
            CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER);
            INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0);
            CREATE TABLE financial_instrument_catalog(ticker TEXT,instrument_type TEXT,market TEXT,
                currency TEXT,settlement TEXT,status TEXT,capability TEXT);
            INSERT INTO financial_instrument_catalog VALUES
                ('AA','ACCIONES','BYMA','ARS','A-24HS','AVAILABLE','READY_PAPER_SPOT'),
                ('BB','ACCIONES','BYMA','ARS','A-24HS','AVAILABLE','UNVERIFIED'),
                ('CC','ACCIONES','BYMA','ARS','A-24HS','AVAILABLE','READY_PAPER_SPOT');
            CREATE TABLE paper_positions(paper_id TEXT,symbol TEXT,asset_class TEXT,market TEXT,
                currency TEXT,settlement TEXT,status TEXT);
            INSERT INTO paper_positions VALUES('p1','AA','ACCIONES','BYMA','ARS','A-24HS','OPEN');
            CREATE TABLE ppi_intraday_contract_state(symbol TEXT,asset_class TEXT,market TEXT,
                currency TEXT,settlement TEXT,state TEXT,last_source_at TEXT,checked_at TEXT);
            CREATE TABLE ppi_intraday_points(symbol TEXT,asset_class TEXT,market TEXT,
                currency TEXT,settlement TEXT,event_at TEXT,first_received_at TEXT,last_verified_at TEXT,
                price,volume,source TEXT);
        """)
        cases = _clock_cases(at)
        connection.executemany("INSERT INTO ppi_intraday_contract_state VALUES(?,?,?,?,?,?,?,?)",
            ((symbol,"ACCIONES","BYMA","ARS","A-24HS",state,last,checked)
             for symbol,state,last,checked,_ in cases))
        for index,(symbol,*_) in enumerate(cases):
            clock = (at-timedelta(seconds=index)).isoformat()
            price = (None, 100, 100.5, "100.75", b"native-price")[index % 5]
            connection.execute("INSERT INTO ppi_intraday_points VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (symbol,"ACCIONES","BYMA","ARS","A-24HS",clock,clock,clock,price,None,"PPI_MARKETDATA_INTRADAY"))
        connection.commit()
        connection.execute("SELECT count(*) FROM ppi_intraday_points").fetchone()
        os.chmod(database, 0o600)
        try:
            yield SimpleNamespace(database=database,as_of=at,cases=cases)
        finally:
            connection.close()


@pytest.mark.parametrize("names", (
    ("ticker","instrument_type","market","currency","settlement","status","capability"),
    ("Ticker","Instrument_Type","Market","Currency","Settlement","Status","Capability"),
))
def test_catalog_tuple_decode_preserves_actual_row_names_and_connection_factory(scalar_source, names):
    before = _source_inventory(scalar_source.database)
    with source_connection(scalar_source.database, deadline=monotonic()+0.5) as (connection,_):
        query = "SELECT " + ",".join('"'+source+'" AS "'+name+'"' for source,name in zip(
            ("ticker","instrument_type","market","currency","settlement","status","capability"),names))
        query += " FROM financial_instrument_catalog ORDER BY ticker"
        original_cursor = connection.execute(query)
        native_row = original_cursor.fetchone()
        actual_names = tuple(native_row.keys())
        assert isinstance(native_row,sqlite3.Row)
        original = [dict(native_row), *(dict(row) for row in original_cursor)]
        proposed_cursor = connection.execute(query)
        assert tuple(column[0] for column in proposed_cursor.description) == actual_names
        decoded = runtime._dict_rows(proposed_cursor)
        assert decoded == original
        assert all(type(row) is dict and tuple(row) == actual_names for row in decoded)
        assert len({id(row) for row in decoded}) == len(decoded)
        assert proposed_cursor.row_factory is None
        assert connection.row_factory is sqlite3.Row
        assert isinstance(connection.execute("SELECT mode FROM observer_state").fetchone(),sqlite3.Row)
    assert _source_inventory(scalar_source.database) == before


@pytest.mark.parametrize("row_limit", (20,3))
def test_read_runtime_matches_frozen_native_row_contract_and_preserves_source(scalar_source, monkeypatch, row_limit):
    before = _source_inventory(scalar_source.database)
    assert "-wal" in before and "-shm" in before
    actual_source_connection = source_connection
    traces, factories = [], []
    @contextmanager
    def observed_source_connection(*args,**kwargs):
        with actual_source_connection(*args,**kwargs) as (connection,source):
            assert connection.row_factory is sqlite3.Row
            trace = []
            connection.set_trace_callback(trace.append)
            yield connection,source
            traces.append(trace)
            factories.append(connection.row_factory)
    monkeypatch.setattr(runtime,"source_connection",observed_source_connection)
    monkeypatch.setitem(_legacy_read_runtime.__globals__,"source_connection",observed_source_connection)
    expected = _legacy_read_runtime(scalar_source.database,as_of=scalar_source.as_of,row_limit=row_limit)
    actual = runtime.read_runtime(scalar_source.database,as_of=scalar_source.as_of,row_limit=row_limit)
    assert actual == expected
    assert traces[0] == traces[1]
    assert factories == [sqlite3.Row,sqlite3.Row]
    assert _source_inventory(scalar_source.database) == before
    if row_limit == 20:
        expected_flags = {symbol:confirmed for symbol,*_,confirmed in scalar_source.cases}
        assert {row["identity"][0]:row["intraday_confirmed"] for row in actual["observations"]} == expected_flags
        assert not actual["observation_read_truncated"]
    else:
        assert len(actual["observations"]) == row_limit and actual["observation_read_truncated"]
    assert [row["ticker"] for row in actual["catalog"]] == ["AA","CC"]
    assert [row["ticker"] for row in actual["full_catalog"]] == ["AA","BB","CC"]
    assert actual["opened"] == [("AA","ACCIONES","BYMA","ARS","A-24HS")]
    assert all(type(row["identity"]) is tuple for row in actual["observations"])


def test_runtime_catalog_outputs_remain_independent_mutable_objects(scalar_source):
    before = _source_inventory(scalar_source.database)
    actual = runtime.read_runtime(scalar_source.database,as_of=scalar_source.as_of)
    all_rows = {row["ticker"]:row for row in actual["full_catalog"]}
    assert all(row is not all_rows[row["ticker"]] for row in actual["catalog"])
    actual["catalog"][0]["capability"] = "LOCAL_TEST_MUTATION"
    assert all_rows["AA"]["capability"] == "READY_PAPER_SPOT"
    actual["full_catalog"][2]["ticker"] = "LOCAL_TEST_MUTATION"
    assert actual["catalog"][1]["ticker"] == "CC"
    fields = [row["fields"] for row in actual["observations"]]
    assert len({id(value) for value in fields}) == len(fields)
    assert _source_inventory(scalar_source.database) == before


def test_contract_timestamp_cache_preserves_short_circuit_and_parse_failures(scalar_source, monkeypatch):
    calls = []
    original_stamp = runtime.stamp
    def observed_stamp(value):
        calls.append(value)
        return original_stamp(value)
    monkeypatch.setattr(runtime,"stamp",observed_stamp)
    actual = runtime.read_runtime(scalar_source.database,as_of=scalar_source.as_of)
    assert "invalid-unvisited" not in calls
    assert "invalid-visited" in calls and "not-a-clock" in calls and None in calls
    flags = {row["identity"][0]:row["intraday_confirmed"] for row in actual["observations"]}
    assert flags == {symbol:confirmed for symbol,*_,confirmed in scalar_source.cases}
    assert flags["BOUND"] is True and flags["SOURCE_STALE"] is False
    assert flags["OFFSET"] is True and flags["NAIVE_SOURCE"] is False
