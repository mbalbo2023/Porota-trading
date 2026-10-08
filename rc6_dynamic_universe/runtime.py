"""Read-only bridge from existing runtime snapshots to SHADOW observation plans."""
import sqlite3
from pathlib import Path
from time import monotonic
from .common import identity, stamp
from rc6_shadow_runtime.source_reads import source_connection
from rc6_shadow_runtime.read_contract import query_budget_seconds as source_query_budget
from rc6_shadow_runtime.read_contract import require_query_budget_binding
import math


def _dict_rows(cursor):
    """Decode explicit catalogue columns without per-row named Row lookups."""
    names = tuple(column[0] for column in cursor.description)
    cursor.row_factory = None
    return [dict(zip(names, row)) for row in cursor]


def read_runtime(database, *, as_of, row_limit=20000, query_budget_seconds=None):
    """No schema/init/writer, history ingestion, provider calls or broker creation.

    Current revisions require last_verified_at at/before cutoff; older values
    cannot be reconstructed from mutable rows. Partial coverage is explicit.
    """
    at = stamp(as_of)
    if query_budget_seconds is None:
        query_budget_seconds = source_query_budget("runtime")
    if (type(row_limit) is not int or not 1 <= row_limit <= 50000
            or isinstance(query_budget_seconds, bool)
            or not isinstance(query_budget_seconds, (int, float))
            or not math.isfinite(query_budget_seconds) or not 0 < query_budget_seconds <= 2):
        raise ValueError("INVALID_READ_BUDGET")
    require_query_budget_binding("runtime", query_budget_seconds)
    deadline = monotonic()+query_budget_seconds
    with source_connection(database, deadline=deadline, consumer="runtime") as (connection, _):
        connection.execute("PRAGMA query_only=ON")
        connection.set_progress_handler(lambda: int(monotonic() > deadline), 1000)
        state = connection.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if not state or state["mode"] != "PRODUCTION_PAPER" or state["real_orders_sent"] != 0:
            raise ValueError("PAPER_SAFETY_REQUIRED")
        catalog = _dict_rows(connection.execute("""SELECT ticker,instrument_type,market,
            currency,settlement,status,capability FROM financial_instrument_catalog
            WHERE status='AVAILABLE' AND capability LIKE 'READY_PAPER%'
            ORDER BY instrument_type,market,currency,ticker,settlement LIMIT ?""", (row_limit+1,)))
        if len(catalog) > row_limit:
            raise ValueError("CATALOG_READ_TRUNCATED")
        full_catalog = _dict_rows(connection.execute("""SELECT ticker,instrument_type,market,
            currency,settlement,status,capability FROM financial_instrument_catalog
            ORDER BY instrument_type,market,currency,ticker,settlement LIMIT ?""", (row_limit+1,)))
        if len(full_catalog) > row_limit:
            raise ValueError("FULL_CATALOG_READ_TRUNCATED")
        opened = [tuple(r) for r in connection.execute("""SELECT symbol,asset_class,market,currency,settlement
            FROM paper_positions WHERE status='OPEN' ORDER BY paper_id LIMIT ?""", (row_limit+1,))]
        if len(opened) > row_limit:
            raise ValueError("OPEN_POSITION_READ_TRUNCATED")
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        observations = []
        if "ppi_intraday_points" in tables:
            from cf_intraday_scalping import require_intraday_temporal_index
            require_intraday_temporal_index(connection)
            contracts = {}
            if "ppi_intraday_contract_state" in tables:
                for r in connection.execute("SELECT * FROM ppi_intraday_contract_state LIMIT ?", (row_limit+1,)):
                    k = tuple(r[x] for x in ("symbol", "asset_class", "market", "currency", "settlement"))
                    try:
                        confirmed = False
                        if r["state"] == "CONFIRMED_INTERVAL_VOLUME":
                            source_at = stamp(r["last_source_at"])
                            if 0 <= (at-source_at).total_seconds() <= 120:
                                checked_at = stamp(r["checked_at"])
                                confirmed = (0 <= (at-checked_at).total_seconds() <= 120 and
                                             source_at <= checked_at)
                    except (ValueError, TypeError):
                        confirmed = False
                    contracts[k] = confirmed
            cursor = connection.execute("""SELECT symbol,asset_class,market,currency,settlement,
                event_at,first_received_at,last_verified_at,price,volume,source FROM ppi_intraday_points
                WHERE julianday(event_at)>=julianday(?) AND julianday(event_at)<=julianday(?)
                  AND julianday(first_received_at)<=julianday(?)
                  AND julianday(last_verified_at)<=julianday(?)
                ORDER BY julianday(event_at) DESC LIMIT ?""", (at.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
                                                    at.isoformat(), at.isoformat(), at.isoformat(), row_limit+1))
            cursor.row_factory = None
            rows = list(cursor)
            for r in rows[:row_limit]:
                key = r[:5]
                observations.append({"identity": key, "source_at": r[5],
                    "received_at": r[7], "source": r[10], "useful": True,
                    "endpoint": "intraday", "intraday_confirmed": contracts.get(key, False),
                    "fields": {"price": r[8]},
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
