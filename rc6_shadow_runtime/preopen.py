"""Bounded, causal input bridge for the separate SHADOW preopen runtime.

This module opens existing SQLite sources in read-only mode. It deliberately
does not instantiate a Store: those constructors negotiate WAL or create
schemas. History Store v2's ``observed_at`` means ingestion, not a market
event. Its append-only versions need a separate explicit source clock; a
date-only canonical row cannot establish one. PPI raw history retains the
provider's daily session label and the time the engine actually received it.
Neither an unknown quantity unit nor interval volume becomes cumulative volume.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta
import json
from math import isfinite
from pathlib import Path
import sqlite3
from time import monotonic
from zoneinfo import ZoneInfo

import ak_byma_calendar as calendar
from co_market_sessions_hf6 import BYMA_PAPER_SPOT_CLOSE, BYMA_PAPER_SPOT_OPEN
from cu_history_store_v2_hf6 import source_rank
from rc6_dynamic_universe.common import identity, stamp

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
IDENTITY_FIELDS = ("ticker", "instrument_type", "market", "currency", "settlement")
MAX_PAYLOAD_BYTES = 2 * 1024 * 1024
MAX_TOTAL_PAYLOAD_BYTES = 16 * 1024 * 1024
DAILY_SESSIONS = 20
INTRADAY_SESSIONS = 5


@contextmanager
def _source(database, deadline):
    path = Path(database).resolve(strict=True)
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=.005)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
        # One coherent read snapshot, never BEGIN IMMEDIATE or journal negotiation.
        connection.execute("BEGIN")
        yield connection
    finally:
        connection.close()


def _tables(connection):
    return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(connection, table):
    # All table names are module constants, never caller-controlled identifiers.
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _number(value, *, positive=False):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if isfinite(result) and (result > 0 if positive else result >= 0) else None


def _object(value):
    if isinstance(value, dict):
        return value
    try:
        result = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return result if isinstance(result, dict) else {}


def _clock_pair(event, available, cutoff, rejected):
    try:
        observed, published = stamp(event), stamp(available)
    except (ValueError, TypeError, OverflowError):
        rejected["SOURCE_TIMESTAMP_NO_VERIFICADO"] += 1
        return None
    if observed > cutoff or published > cutoff:
        rejected["FUTURE_INFORMATION_REJECTED"] += 1
        return None
    if published < observed:
        rejected["SOURCE_CLOCK_ORDER_INVALID"] += 1
        return None
    return observed.isoformat(), published.isoformat()


def _availability(stored_at, *records):
    """Keep actual engine receipt and any later explicit publication clock."""
    clocks = [stamp(stored_at)]
    for record in records:
        for name in ("published_at", "provider_published_at", "available_to_engine_at"):
            if name in record:
                clocks.append(stamp(record[name]))
    return max(clocks).isoformat()


def _audited_sessions(cutoff, opening, as_of):
    local_open = opening.astimezone(TZ)
    if (local_open.date().year not in calendar.ANIOS_AUDITADOS
            or not calendar.es_dia_habil_operativo(local_open.date())
            or as_of.astimezone(TZ).date() < calendar.CALENDARIO_AUDITADO_EL):
        return [], "AUDITED_CALENDAR_NO_VERIFICADO"
    result = []
    day = min(local_open.date() - timedelta(days=1), cutoff.astimezone(TZ).date())
    for _ in range(400):
        if day.year not in calendar.ANIOS_AUDITADOS:
            break
        close = datetime.combine(day, BYMA_PAPER_SPOT_CLOSE, TZ)
        if calendar.es_dia_habil_operativo(day) and close <= cutoff:
            result.append(day.isoformat())
            if len(result) == DAILY_SESSIONS:
                break
        day -= timedelta(days=1)
    return sorted(result), "VERIFIED_INPUT" if len(result) == DAILY_SESSIONS else "AUDITED_CALENDAR_INCOMPLETE"


def _identity_indexes(catalog, source_catalog):
    by_request, by_history = defaultdict(list), defaultdict(list)
    complete = {identity(record) for record in catalog}
    # Ambiguity belongs to the source request, including a non-READY sibling.
    # Readiness cannot make a provider route currency-qualified retroactively.
    for record in source_catalog:
        try:
            key = identity(record)
        except (ValueError, TypeError):
            continue
        by_request[(key[0], key[1], key[4])].append(key)
        by_history[(key[0], key[1], key[2], key[4])].append(key)
    return complete, by_request, by_history


def _resolve_identity(symbol, family, settlement, metadata, indexes, rejected, *, market=None):
    complete, by_request, by_history = indexes
    symbol, family, settlement = (str(value or "").strip().upper()
                                  for value in (symbol, family, settlement))
    explicit_market = str(metadata.get("market") or market or "").strip().upper()
    explicit_currency = str(metadata.get("currency") or "").strip().upper()
    candidates = (by_history.get((symbol, family, explicit_market, settlement), [])
                  if explicit_market else by_request.get((symbol, family, settlement), []))
    if explicit_currency:
        candidates = [key for key in candidates if key[3] == explicit_currency]
    if len(candidates) != 1 or candidates[0] not in complete:
        rejected["SOURCE_IDENTITY_NO_VERIFICADO"] += 1
        return None
    return dict(zip(IDENTITY_FIELDS, candidates[0]))


def _daily_metrics(row, metadata, key, rejected):
    unit = str(row.get("volume_unit") or row.get("volumeUnit")
               or metadata.get("volume_unit") or "NO_VERIFICADO").upper()
    volume = _number(row.get("volume"))
    turnover_currency = str(row.get("turnover_currency") or row.get("turnoverCurrency")
                            or metadata.get("turnover_currency") or "NO_VERIFICADO").upper()
    result = {"volume": volume, "volume_unit": unit,
              "turnover": _number(row.get("turnover", metadata.get("turnover"))), "turnover_currency": turnover_currency}
    if volume is not None and unit not in {"SHARES", "UNITS"}:
        rejected["VOLUME_UNIT_NO_VERIFICADO"] += 1
    if result["turnover"] is not None and turnover_currency != key["currency"]:
        rejected["TURNOVER_CURRENCY_NO_VERIFICADO"] += 1
    for field in ("trades", "interarrival_seconds", "range_bps"):
        value = _number(row.get(field, metadata.get(field)))
        if value is not None:
            result[field] = value
    if "trades" not in result:
        trades = _number(row.get("tradeCount", metadata.get("trade_count")))
        if trades is not None:
            result["trades"] = trades
    high = _number(row.get("high", row.get("max")), positive=True)
    low = _number(row.get("low", row.get("min")), positive=True)
    close = _number(row.get("close", row.get("price")), positive=True)
    if "range_bps" not in result and high is not None and low is not None and close is not None and low <= close <= high:
        result["range_bps"] = (high - low) / close * 10000
    return result


def _fetch(connection, query, parameters, limit, quality, source, *, payload_field=None,
           byte_budget=None, deadline=None):
    rows, byte_count, count = [], 0, 0
    for row in connection.execute(query, (*parameters, limit + 1)):
        if count == limit:
            quality["truncated_sources"].append(source)
            break
        count += 1
        if payload_field is not None:
            body = row[payload_field]
            # The SQL CASE supplies NULL for oversized payloads, preventing
            # even one unbounded blob from entering the Python process.
            if body is None:
                quality["truncated_sources"].append(source+"_oversized_payload")
                continue
            size = len(body.encode("utf-8"))
            if byte_count+size > byte_budget or monotonic() >= deadline:
                quality["truncated_sources"].append(source+"_payload_budget")
                break
            byte_count += size
        rows.append(row)
    quality["source_rows_read"][source] = quality["source_rows_read"].get(source, 0) + count
    return rows


def _raw_daily(payload, metadata, *, available_at, source, indexes, sessions, cutoff, rejected):
    if not isinstance(payload, list):
        rejected["HISTORY_PAYLOAD_NO_VERIFICADO"] += 1
        return []
    key = _resolve_identity(metadata.get("ticker", metadata.get("symbol")),
                            metadata.get("instrument_type", metadata.get("asset_class")),
                            metadata.get("settlement"), metadata, indexes, rejected)
    if key is None:
        return []
    result = []
    for raw in payload:
        if not isinstance(raw, dict):
            rejected["HISTORY_ROW_NO_VERIFICADO"] += 1
            continue
        # Provider date is a daily session label; never synthesize midnight or
        # closing timestamps when its timezone/clock is absent.
        session = str(raw.get("session") or raw.get("date") or "")[:10]
        if session not in sessions:
            rejected["SESSION_OUTSIDE_AUDITED_CUT"] += 1
            continue
        try:
            availability = _availability(available_at, metadata, raw)
        except (ValueError, TypeError, OverflowError):
            rejected["SOURCE_TIMESTAMP_NO_VERIFICADO"] += 1
            continue
        clocks = _clock_pair(raw.get("source_at", raw.get("date")), availability, cutoff, rejected)
        if clocks is None:
            continue
        try:
            if date.fromisoformat(session) != date.fromisoformat(clocks[0][:10]):
                raise ValueError("SESSION_SOURCE_CLOCK_MISMATCH")
        except ValueError:
            rejected["SESSION_SOURCE_CLOCK_MISMATCH"] += 1
            continue
        # A daily total cannot have been received before that audited session
        # closed even if the provider's label denotes its start.
        if stamp(clocks[1]) < datetime.combine(date.fromisoformat(session), BYMA_PAPER_SPOT_CLOSE, TZ):
            rejected["DAILY_SESSION_NOT_CLOSED_AT_AVAILABILITY"] += 1
            continue
        result.append({**key, "session": session, "observed_at": clocks[0], "published_at": clocks[1],
                       "source": source, "source_clock_meaning": "PROVIDER_DAILY_SESSION_LABEL",
                       **_daily_metrics(raw, metadata, key, rejected)})
    return result


def _read_daily(connection, tables, indexes, sessions, cutoff, quality, rejected, limit, deadline):
    records, payload_bytes = [], 0
    cut = cutoff.isoformat()
    # The append-only raw archive preserves prior versions even when the latest
    # mutable production_history payload was downloaded after the preopen cut.
    if "historical_raw_archive" in tables:
        rows = _fetch(connection, f"""SELECT CASE WHEN length(CAST(body_json AS BLOB))<={MAX_PAYLOAD_BYTES}
          THEN body_json END AS body_json,recorded_at FROM historical_raw_archive
          WHERE origin='PPI_HISTORY' AND julianday(recorded_at)<=julianday(?)
          ORDER BY julianday(recorded_at) DESC,id DESC LIMIT ?""", (cut,), min(limit, 1000), quality, "historical_raw_archive",
          payload_field="body_json", byte_budget=MAX_TOTAL_PAYLOAD_BYTES, deadline=deadline)
        for row in rows:
            size = len(row["body_json"].encode("utf-8"))
            if size > MAX_PAYLOAD_BYTES or payload_bytes + size > MAX_TOTAL_PAYLOAD_BYTES or monotonic() >= deadline:
                quality["truncated_sources"].append("historical_raw_archive_payload_budget")
                break
            payload_bytes += size
            wrapper = _object(row["body_json"])
            metadata = {**_object(wrapper.get("metadata")), **{name: wrapper[name] for name in ("symbol", "asset_class", "settlement") if name in wrapper}}
            try:
                payload = json.loads(wrapper.get("payload_json"))
            except (ValueError, TypeError):
                rejected["HISTORY_PAYLOAD_NO_VERIFICADO"] += 1
                continue
            records.extend(_raw_daily(payload, metadata, available_at=row["recorded_at"],
                source="PPI_PRODUCTION_HISTORY", indexes=indexes, sessions=sessions, cutoff=cutoff, rejected=rejected))
            if len(records) > limit:
                quality["truncated_sources"].append("daily_output_budget")
                records = records[:limit]
                break
    if "production_history" in tables and len(records) < limit:
        rows = _fetch(connection, f"""SELECT symbol,instrument_type,settlement,downloaded_at,
          CASE WHEN length(CAST(payload_json AS BLOB))<={MAX_PAYLOAD_BYTES}
          THEN payload_json END AS payload_json
          FROM production_history WHERE julianday(downloaded_at)<=julianday(?) AND row_count>0
          ORDER BY julianday(downloaded_at) DESC,symbol,instrument_type,settlement LIMIT ?""",
          (cut,), min(limit, 10000), quality, "production_history", payload_field="payload_json",
          byte_budget=MAX_TOTAL_PAYLOAD_BYTES-payload_bytes, deadline=deadline)
        for row in rows:
            body = row["payload_json"] or ""
            size = len(body.encode("utf-8"))
            if size > MAX_PAYLOAD_BYTES or payload_bytes + size > MAX_TOTAL_PAYLOAD_BYTES or monotonic() >= deadline:
                quality["truncated_sources"].append("production_history_payload_budget")
                break
            payload_bytes += size
            try:
                payload = json.loads(body)
            except (ValueError, TypeError):
                rejected["HISTORY_PAYLOAD_NO_VERIFICADO"] += 1
                continue
            records.extend(_raw_daily(payload, dict(row), available_at=row["downloaded_at"],
                source="PPI_PRODUCTION_HISTORY", indexes=indexes, sessions=sessions, cutoff=cutoff, rejected=rejected))
            if len(records) > limit:
                quality["truncated_sources"].append("daily_output_budget")
                records = records[:limit]
                break
    quality["payload_bytes_read"] = payload_bytes
    return records


def _read_history_versions(connection, tables, indexes, sessions, cutoff, quality, rejected, limit):
    if "history_versions_v2" not in tables:
        if "history_canonical_v2" in tables:
            rejected["APPEND_ONLY_HISTORY_VERSIONS_UNAVAILABLE"] += 1
        return []
    slots = ",".join("?" for _ in sessions)
    rows = _fetch(connection, f"""SELECT * FROM history_versions_v2
      WHERE date IN ({slots}) AND julianday(observed_at)<=julianday(?)
      ORDER BY date DESC,julianday(observed_at) DESC,id DESC LIMIT ?""",
      (*sessions, cutoff.isoformat()), limit, quality, "history_versions_v2")
    records = []
    for sqlite_row in rows:
        row = dict(sqlite_row)
        metadata = _object(row.get("metadata_json"))
        event = metadata.get("source_at") or metadata.get("event_at") or metadata.get("provider_observed_at")
        if not event:
            rejected["HISTORY_SOURCE_EVENT_TIME_NO_VERIFICADO"] += 1
            continue
        # The stored clock is the first engine availability of this immutable
        # version; an earlier provider publication never backdates availability.
        try:
            availability = _availability(row["observed_at"], metadata)
        except (ValueError, TypeError, OverflowError):
            rejected["SOURCE_TIMESTAMP_NO_VERIFICADO"] += 1
            continue
        clocks = _clock_pair(event, availability, cutoff, rejected)
        if clocks is None:
            continue
        if clocks[0][:10] != row["date"]:
            rejected["SESSION_SOURCE_CLOCK_MISMATCH"] += 1
            continue
        if stamp(clocks[1]) < datetime.combine(date.fromisoformat(row["date"]), BYMA_PAPER_SPOT_CLOSE, TZ):
            rejected["DAILY_SESSION_NOT_CLOSED_AT_AVAILABILITY"] += 1
            continue
        key = _resolve_identity(row["symbol"], row["instrument_type"], row["settlement"], metadata,
                                indexes, rejected, market=row["market"])
        if key is not None:
            records.append({**key, "session": row["date"], "observed_at": clocks[0], "published_at": clocks[1],
                            "source": row["source"], "adjusted": bool(row["adjusted"]),
                            "source_clock_meaning": "EXPLICIT_PROVIDER_EVENT",
                            **_daily_metrics(row, metadata, key, rejected)})
    return records


def _read_books(connection, tables, indexes, sessions, cutoff, quality, rejected, limit):
    if "market_snapshots" not in tables:
        return []
    needed = {"symbol", "asset_class", "market", "currency", "settlement", "observed_at", "book_at", "bid", "ask", "bid_size", "ask_size"}
    if not needed <= _columns(connection, "market_snapshots"):
        rejected["BOOK_SCHEMA_NO_VERIFICADO"] += 1
        return []
    rows = _fetch(connection, """SELECT * FROM market_snapshots
      WHERE julianday(observed_at)<=julianday(?) AND julianday(book_at)<=julianday(?)
        AND julianday(book_at)>=julianday(?)
      ORDER BY id DESC LIMIT ?""", (cutoff.isoformat(), cutoff.isoformat(), (cutoff-timedelta(minutes=15)).isoformat()),
      limit, quality, "market_snapshots")
    records = []
    for sqlite_row in rows:
        row = dict(sqlite_row)
        clocks = _clock_pair(row["book_at"], row["observed_at"], cutoff, rejected)
        if clocks is None:
            continue
        session = stamp(clocks[0]).astimezone(TZ).date().isoformat()
        if session not in sessions:
            rejected["SESSION_OUTSIDE_AUDITED_CUT"] += 1
            continue
        key = _resolve_identity(row["symbol"], row["asset_class"], row["settlement"], row, indexes, rejected)
        if key is None:
            continue
        bid, ask = _number(row["bid"], positive=True), _number(row["ask"], positive=True)
        bid_size, ask_size = _number(row["bid_size"]), _number(row["ask_size"])
        usable = bid is not None and ask is not None and bid <= ask and bid_size is not None and ask_size is not None and min(bid_size, ask_size) > 0
        # The productive contract has no quantity-unit field. Only an explicit
        # source attestation can establish a unit, never the family or ticker.
        contract = _object(row.get("contract_json"))
        unit = str(row.get("depth_unit") or contract.get("quantity_unit") or "NO_VERIFICADO").upper()
        if unit not in {"SHARES", "UNITS"}:
            rejected["DEPTH_UNIT_NO_VERIFICADO"] += 1
        records.append({**key, "session": session, "observed_at": clocks[0], "published_at": clocks[1],
                        "source": row.get("source") or "PPI_MARKETDATA_BOOK", "usable": usable,
                        "spread_bps": (ask-bid)/((ask+bid)/2)*10000 if bid is not None and ask is not None and bid <= ask else None,
                        "depth_units": min(bid_size, ask_size) if bid_size is not None and ask_size is not None else None,
                        "depth_unit": unit})
    return records


def _read_intraday(connection, tables, indexes, sessions, cutoff, quality, rejected, limit):
    if "ppi_intraday_points" not in tables:
        return []
    recent = sessions[-INTRADAY_SESSIONS:]
    earliest = datetime.combine(date.fromisoformat(recent[0]), BYMA_PAPER_SPOT_OPEN, TZ)
    rows = _fetch(connection, """SELECT * FROM ppi_intraday_points
      WHERE julianday(event_at)>=julianday(?) AND julianday(event_at)<=julianday(?)
        AND julianday(first_received_at)<=julianday(?) AND julianday(last_verified_at)<=julianday(?)
      ORDER BY julianday(event_at) DESC,symbol,asset_class,market,currency,settlement LIMIT ?""",
      (earliest.isoformat(), cutoff.isoformat(), cutoff.isoformat(), cutoff.isoformat()),
      limit, quality, "ppi_intraday_points")
    records = []
    for sqlite_row in rows:
        row = dict(sqlite_row)
        clocks = _clock_pair(row["event_at"], row["last_verified_at"], cutoff, rejected)
        if clocks is None:
            continue
        try:
            first = stamp(row["first_received_at"])
            if not stamp(clocks[0]) <= first <= stamp(clocks[1]):
                raise ValueError("INTRADAY_AVAILABILITY_CLOCK_ORDER_INVALID")
        except (ValueError, TypeError, OverflowError):
            rejected["INTRADAY_AVAILABILITY_CLOCK_ORDER_INVALID"] += 1
            continue
        observed = stamp(clocks[0]).astimezone(TZ)
        session = observed.date().isoformat()
        if session not in recent:
            rejected["SESSION_OUTSIDE_AUDITED_CUT"] += 1
            continue
        minute = (observed-datetime.combine(observed.date(), BYMA_PAPER_SPOT_OPEN, TZ)).total_seconds()/60
        if minute < 0 or minute >= 390 or minute != int(minute):
            rejected["INTRADAY_SESSION_MINUTE_NO_VERIFICADO"] += 1
            continue
        key = _resolve_identity(row["symbol"], row["asset_class"], row["settlement"], row, indexes, rejected)
        price = _number(row["price"], positive=True)
        if key is None or price is None:
            rejected["INTRADAY_PRICE_NO_VERIFICADO"] += int(price is None)
            continue
        record = {**key, "session": session, "minute_of_session": int(minute),
                  "observed_at": clocks[0], "published_at": clocks[1], "source": row["source"],
                  "price": price, "volume_unit": "NO_VERIFICADO", "volume_semantics": "NO_VERIFICADO"}
        # Most productive rows contain interval volume without an explicit unit.
        # A cumulative profile is accepted only if both meanings are explicit.
        unit = str(row.get("volume_unit") or "NO_VERIFICADO").upper()
        semantics = str(row.get("volume_semantics") or "NO_VERIFICADO").upper()
        cumulative = _number(row.get("cumulative_volume"))
        if unit in {"SHARES", "UNITS"} and semantics == "CUMULATIVE" and cumulative is not None:
            record.update(volume_unit=unit, volume_semantics=semantics, cumulative_volume=cumulative)
        else:
            rejected["INTRADAY_VOLUME_PROFILE_NO_VERIFICADO"] += 1
        records.append(record)
    return records


def _chosen_daily(records):
    # Reconstruct History Store authority at the cut; eventual canonical rows
    # or a later, lower-authority fallback cannot overwrite a prior PPI version.
    chosen = {}
    for row in records:
        key = (identity(row), row["session"])
        ordering = (bool(row.get("adjusted")), -source_rank(row["source"]), stamp(row["published_at"]), stamp(row["observed_at"]))
        if key not in chosen or ordering > chosen[key][0]:
            chosen[key] = ordering, row
    return [chosen[key][1] for key in sorted(chosen)]


def build_preopen_inputs(database, history_database=None, *, as_of, session_open, cutoff,
                         row_limit=50000, query_budget_seconds=2.0):
    """Read real previous-session inputs for rank_tradeability/anomaly_events.

    Output never filters or mutates the catalogue. All source/publication
    clocks must be at/before cutoff, and all sessions must be prior audited BYMA
    sessions. A bounded/locked/missing source is explicit in ``quality``. No
    Store constructor, provider call, backfill, schema change or source write.
    """
    at, opening, cut = stamp(as_of), stamp(session_open), stamp(cutoff)
    if not cut < at < opening or cut.date() >= opening.date():
        raise ValueError("PREOPEN_CUT_READ_OPEN_ORDER_REQUIRED")
    if not 1 <= row_limit <= 100000 or not 0 < query_budget_seconds <= 2:
        raise ValueError("INVALID_PREOPEN_READ_BUDGET")
    sessions, calendar_status = _audited_sessions(cut, opening, at)
    quality = {"status": "NO_VERIFICADO", "calendar_status": calendar_status,
               "calendar_source": calendar.FUENTE_OFICIAL,
               "calendar_audited_at": calendar.CALENDARIO_AUDITADO_EL.isoformat(),
               "cutoff": cut.isoformat(), "source_database_effect": "READ_ONLY",
               "history_database_effect": "READ_ONLY" if history_database is not None else "NOT_CONFIGURED",
               "source_rows_read": {}, "truncated_sources": [], "unavailable_sources": [],
               "catalog_view": "ALL_READY_PAPER_IDENTITIES_UNCHANGED",
               "daily_clock_policy": "EXPLICIT_PROVIDER_CLOCK_AND_REAL_ENGINE_AVAILABILITY",
               "intraday_revision_policy": "CURRENT_STORED_REVISION_AVAILABLE_BY_CUTOFF",
               "volume_policy": "EXPLICIT_UNIT_AND_SEMANTICS_REQUIRED",
               "real_orders_sent": 0}
    rejected = Counter()
    records, books, intraday = [], [], []
    deadline = monotonic()+query_budget_seconds
    # Safety failures and catalogue truncation are contract violations, not
    # ordinary unavailable optional evidence, and must propagate to the worker.
    try:
        with _source(database, deadline) as connection:
            tables = _tables(connection)
            state = connection.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
            if not state or state["mode"] != "PRODUCTION_PAPER" or state["real_orders_sent"] != 0:
                raise ValueError("PAPER_SAFETY_REQUIRED")
            catalog = _fetch(connection, """SELECT ticker,instrument_type,market,currency,settlement
              FROM financial_instrument_catalog WHERE status='AVAILABLE' AND capability LIKE 'READY_PAPER%'
              ORDER BY instrument_type,market,currency,ticker,settlement LIMIT ?""", (), row_limit, quality, "catalog")
            if "catalog" in quality["truncated_sources"]:
                raise ValueError("CATALOG_READ_TRUNCATED")
            source_catalog = _fetch(connection, """SELECT ticker,instrument_type,market,currency,settlement
              FROM financial_instrument_catalog WHERE status='AVAILABLE'
              ORDER BY instrument_type,market,currency,ticker,settlement LIMIT ?""", (), row_limit, quality, "source_catalog")
            if "source_catalog" in quality["truncated_sources"]:
                raise ValueError("SOURCE_CATALOG_READ_TRUNCATED")
            indexes = _identity_indexes([dict(row) for row in catalog], [dict(row) for row in source_catalog])
            quality["ready_catalog_count"] = len(catalog)
            if sessions:
                records.extend(_read_daily(connection, tables, indexes, set(sessions), cut, quality, rejected, row_limit, deadline))
                records.extend(_read_history_versions(connection, tables, indexes, sessions, cut, quality, rejected, row_limit))
                books = _read_books(connection, tables, indexes, set(sessions), cut, quality, rejected, row_limit)
                intraday = _read_intraday(connection, tables, indexes, sessions, cut, quality, rejected, row_limit)
    except (sqlite3.Error, OSError) as exc:
        quality["unavailable_sources"].append(f"trading:{type(exc).__name__}")
    if history_database is not None and sessions and "indexes" in locals():
        try:
            # Opening a second read-only source cannot create a missing history
            # DB or renegotiate its journal mode. Repeated aliases are harmless.
            with _source(history_database, deadline) as connection:
                records.extend(_read_history_versions(connection, _tables(connection), indexes, sessions,
                                                      cut, quality, rejected, row_limit))
        except (sqlite3.Error, OSError) as exc:
            quality["unavailable_sources"].append(f"history:{type(exc).__name__}")
    history = _chosen_daily(records)
    quality["truncated_sources"] = sorted(set(quality["truncated_sources"]))
    quality["rejected_inputs"] = dict(sorted(rejected.items()))
    quality["accepted_rows"] = {"history": len(history), "preopen_observations": len(books), "intraday_history": len(intraday)}
    if (calendar_status == "VERIFIED_INPUT" and history and books
            and not quality["truncated_sources"] and not quality["unavailable_sources"]
            and not any("NO_VERIFICADO" in reason for reason in rejected)):
        quality["status"] = "VERIFIED_INPUT"
    return {"history": history, "preopen_observations": books, "sessions": sessions,
            "intraday_history": intraday, "quality": quality}
