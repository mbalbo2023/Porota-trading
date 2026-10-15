"""Read-only bridge from existing runtime snapshots to SHADOW observation plans."""
import sqlite3
from pathlib import Path
from time import monotonic
from .common import identity, stamp


def read_runtime(database, *, as_of, row_limit=20000, query_budget_seconds=0.5,
                 diagnostics=None):
    """No schema/init/writer, history ingestion, provider calls or broker creation.

    Current revisions require last_verified_at at/before cutoff; older values
    cannot be reconstructed from mutable rows. Partial coverage is explicit.
    """
    at = stamp(as_of)
    if not 1 <= row_limit <= 50000 or not 0 < query_budget_seconds <= 2:
        raise ValueError("INVALID_READ_BUDGET")
    path = Path(database).resolve(strict=True)
    connection = sqlite3.connect(path.as_uri()+"?mode=ro", uri=True, timeout=.005)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        # One WAL snapshot for all safety, catalogue and observation reads.
        # Release it before the much larger in-memory observation conversion.
        connection.execute("BEGIN")
        deadline = monotonic()+query_budget_seconds
        interrupted_by_deadline = False
        if diagnostics is not None:
            diagnostics.update(budget_ms=round(query_budget_seconds*1000), stages=[])

        def progress():
            nonlocal interrupted_by_deadline
            if monotonic() > deadline:
                interrupted_by_deadline = True
                return 1
            return 0

        def read_rows(stage, sql, parameters=()):
            started = monotonic()
            rows = []
            error = None
            try:
                if diagnostics is not None:
                    diagnostics.setdefault("plans", {})[stage] = [
                        str(row[3])[:120] for row in connection.execute(
                            "EXPLAIN QUERY PLAN " + sql, parameters)]
                for row in connection.execute(sql, parameters):
                    rows.append(row)
                return rows
            except sqlite3.Error as exc:
                error = exc
                # Preserve the SQLite class/code while adding only bounded,
                # non-sensitive stage context for the fail-closed status.
                exc.rc6_stage = stage
                exc.rc6_deadline_expired = interrupted_by_deadline
                raise
            finally:
                if diagnostics is not None:
                    detail = {"stage": stage, "elapsed_ms": round((monotonic()-started)*1000, 3),
                              "rows": len(rows),
                              "remaining_ms": round(max(0, deadline-monotonic())*1000, 3),
                              "deadline_elapsed": monotonic() > deadline,
                              "interrupted_by_deadline": interrupted_by_deadline}
                    if error is not None:
                        detail["sqlite_errorname"] = getattr(error, "sqlite_errorname", "SQLITE_UNKNOWN")
                    diagnostics["stages"].append(detail)

        connection.set_progress_handler(progress, 1000)
        state = connection.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if not state or state["mode"] != "PRODUCTION_PAPER" or state["real_orders_sent"] != 0:
            raise ValueError("PAPER_SAFETY_REQUIRED")
        # Both views have the same ordered columns. Read the full bounded
        # catalogue once and derive READY locally: a second SQLite scan can
        # exceed the shared deadline when the observer is under IO contention.
        full_catalog_rows = read_rows("CATALOG_FULL", """SELECT ticker,instrument_type,market,
            currency,settlement,status,capability FROM financial_instrument_catalog
            ORDER BY instrument_type,market,currency,ticker,settlement LIMIT ?""", (row_limit+1,))
        if len(full_catalog_rows) > row_limit:
            raise ValueError("FULL_CATALOG_READ_TRUNCATED")
        opened_rows = read_rows("OPEN_POSITIONS", """SELECT symbol,asset_class,market,currency,settlement
            FROM paper_positions WHERE status='OPEN' ORDER BY paper_id LIMIT ?""", (row_limit+1,))
        if len(opened_rows) > row_limit:
            raise ValueError("OPEN_POSITION_READ_TRUNCATED")
        tables = {r[0] for r in read_rows("SCHEMA_TABLES", "SELECT name FROM sqlite_master WHERE type='table'")}
        contracts_rows = []
        intraday_rows = []
        snapshot_rows = []
        if "ppi_intraday_points" in tables:
            if "ppi_intraday_contract_state" in tables:
                contracts_rows = read_rows("INTRADAY_CONTRACTS", "SELECT * FROM ppi_intraday_contract_state LIMIT ?", (row_limit+1,))
            intraday_rows = read_rows("INTRADAY_POINTS", """SELECT symbol,asset_class,market,currency,settlement,
                event_at,first_received_at,last_verified_at,price,volume,source FROM ppi_intraday_points
                WHERE julianday(event_at)>=julianday(?) AND julianday(event_at)<=julianday(?)
                  AND julianday(first_received_at)<=julianday(?)
                  AND julianday(last_verified_at)<=julianday(?)
                ORDER BY julianday(event_at) DESC LIMIT ?""", (at.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
                                                    at.isoformat(), at.isoformat(), at.isoformat(), row_limit+1))
        if "market_snapshots" in tables:
            snapshot_rows = read_rows("MARKET_SNAPSHOTS", """SELECT symbol,asset_class,market,currency,settlement,
                last,trade_at,book_at,observed_at,bid,ask,bid_size,ask_size,last_kind FROM market_snapshots NOT INDEXED
                WHERE id >= (SELECT MIN(id) FROM market_snapshots
                    WHERE julianday(observed_at)<=julianday(?)
                      AND julianday(observed_at)>=julianday(?))
                  AND julianday(observed_at)<=julianday(?)
                  AND julianday(observed_at)>=julianday(?)
                ORDER BY id DESC LIMIT ?""", (at.isoformat(),
                    at.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
                    at.isoformat(),
                    at.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
                    row_limit+1))
        connection.rollback()
        if diagnostics is not None:
            diagnostics["sql_elapsed_ms"] = round((monotonic() - (deadline-query_budget_seconds))*1000, 3)
            diagnostics["sql_remaining_ms"] = round(max(0, deadline-monotonic())*1000, 3)
        transform_started = monotonic()
        transform_deadline = transform_started + 2.0

        def transform_guard(stage):
            if monotonic() > transform_deadline:
                if diagnostics is not None:
                    diagnostics["transform_elapsed_ms"] = round((monotonic()-transform_started)*1000, 3)
                    diagnostics["transform_expired_stage"] = stage
                exc = ValueError("RUNTIME_TRANSFORM_BUDGET_EXCEEDED")
                exc.rc6_stage = stage
                raise exc
        # Transform only after all SQL cursors have finished. In the previous
        # layout, thousands of timestamp parses consumed the SQLite deadline
        # between queries and interrupted a later, otherwise indexed read.
        full_catalog = [dict(r) for r in full_catalog_rows]
        catalog = [row for row in full_catalog if row["status"] == "AVAILABLE"
                   and row["capability"].upper().startswith("READY_PAPER")]
        transform_guard("CATALOG_FULL")
        opened = [tuple(r) for r in opened_rows]
        observations = []
        if "ppi_intraday_points" in tables:
            contracts = {}
            for r in contracts_rows:
                k = tuple(r[x] for x in ("symbol", "asset_class", "market", "currency", "settlement"))
                try:
                    source_at = stamp(r["last_source_at"])
                    checked_at = stamp(r["checked_at"])
                    confirmed = (r["state"] == "CONFIRMED_INTERVAL_VOLUME" and
                        0 <= (at-source_at).total_seconds() <= 120 and
                        0 <= (at-checked_at).total_seconds() <= 120 and source_at <= checked_at)
                except (ValueError, TypeError):
                    confirmed = False
                contracts[k] = confirmed
                transform_guard("INTRADAY_CONTRACTS")
            for r in intraday_rows[:row_limit]:
                key = tuple(r[k] for k in ("symbol", "asset_class", "market", "currency", "settlement"))
                observations.append({"identity": key, "source_at": r["event_at"],
                    "received_at": r["last_verified_at"], "source": r["source"], "useful": True,
                    "endpoint": "intraday", "intraday_confirmed": contracts.get(key, False),
                    "fields": {"price": r["price"]},
                    "volume_semantics": "INTERVAL_VOLUME" if contracts.get(key, False) else "NO_VERIFICADO",
                    "volume_unit": "NO_VERIFICADO", "entry_authority": False})
                transform_guard("INTRADAY_POINTS")
            truncated = len(intraday_rows) > row_limit
        else:
            truncated = False
        if "market_snapshots" in tables:
            for r in snapshot_rows[:row_limit]:
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
                transform_guard("MARKET_SNAPSHOTS")
            truncated |= len(snapshot_rows) > row_limit
        transform_guard("COMPLETE")
        if diagnostics is not None:
            diagnostics["transform_elapsed_ms"] = round((monotonic()-transform_started)*1000, 3)
        return {"catalog": catalog, "full_catalog": full_catalog, "opened": opened, "observations": observations,
                "catalog_view": "ALL_READY_PAPER_IDENTITIES; financial catalogue unchanged",
                "observation_read_truncated": truncated,
                "safety": {"mode": "PRODUCTION_PAPER", "real_orders_sent": 0,
                           "real_routes": "NOT_CALLED"}, "source_database_effect": "READ_ONLY"}
    finally:
        connection.close()
