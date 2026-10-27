"""Bounded, read-only source capture for a new RC6 history generation.

This is an evidence store, not a migration or a trading/history-v2 writer.
No provider row is assigned a currency, adjustment basis or past PIT clock from
the requested instrument. Captures can be inspected before any separate,
reviewed promotion. The legacy database and all runtime consumers stay put.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import stat
from datetime import date, datetime, timezone
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo

from bs_instrument_contracts import cash_currency
from cu_history_store_v2_hf6 import instant
from rc6_data_retention import daily_start

ART = ZoneInfo("America/Argentina/Buenos_Aires")
SOURCES = {"PPI", "IOL", "IOL_CONNECTOR", "DATA912", "A3_CEM", "BYMA"}
MAX_TARGETS = 40
MAX_ROWS = 1000
MAX_PAYLOAD_BYTES = 10_000_000
MAX_CLOCK_SKEW_SECONDS = 300

DDL = """
CREATE TABLE fresh_generation_rc6(
  id INTEGER PRIMARY KEY CHECK(id=1), created_at TEXT NOT NULL,
  cutoff_day TEXT NOT NULL, policy TEXT NOT NULL CHECK(policy='EVIDENCE_ONLY_365_ART_V1'));
CREATE TABLE fresh_captures_rc6(
  id INTEGER PRIMARY KEY, source TEXT NOT NULL, symbol TEXT NOT NULL,
  instrument_type TEXT NOT NULL, market TEXT NOT NULL, currency TEXT NOT NULL,
  settlement TEXT NOT NULL, requested_from TEXT NOT NULL, requested_to TEXT NOT NULL,
  first_known_at TEXT NOT NULL, canonical_payload_sha256 TEXT NOT NULL,
  canonical_payload_bytes INTEGER NOT NULL, retained_rows_json TEXT NOT NULL,
  retained_rows INTEGER NOT NULL, dropped_expired INTEGER NOT NULL,
  dropped_invalid INTEGER NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL,
  provider_currency_status TEXT NOT NULL, price_basis_status TEXT NOT NULL,
  readiness_implication TEXT NOT NULL CHECK(readiness_implication='NONE'),
  execution_allowed INTEGER NOT NULL CHECK(execution_allowed=0));
CREATE INDEX fresh_captures_key_rc6 ON fresh_captures_rc6(
  source,symbol,instrument_type,market,currency,settlement,first_known_at);
CREATE TABLE fresh_row_revisions_rc6(
  id INTEGER PRIMARY KEY, capture_id INTEGER NOT NULL,
  retained_ordinal INTEGER NOT NULL, provider_row_index INTEGER NOT NULL,
  economic_day TEXT NOT NULL, canonical_row_sha256 TEXT NOT NULL,
  first_known_at TEXT NOT NULL,
  UNIQUE(capture_id,retained_ordinal),
  FOREIGN KEY(capture_id) REFERENCES fresh_captures_rc6(id));
"""


def _body(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _target(raw):
    if not isinstance(raw, (list, tuple)) or len(raw) != 5:
        raise ValueError("FRESH_EXACT_IDENTITY_REQUIRED")
    target = tuple(str(part or "").strip().upper() for part in raw)
    if any(part in {"", "UNKNOWN"} for part in target):
        raise ValueError("FRESH_EXACT_IDENTITY_REQUIRED")
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{0,39}", target[0]):
        raise ValueError("FRESH_SYMBOL_INVALID")
    try:
        if cash_currency(target[3]) != target[3]:
            raise ValueError("FRESH_CURRENCY_UNVERIFIED")
    except ValueError as exc:
        raise ValueError("FRESH_CURRENCY_UNVERIFIED") from exc
    return target


def _request_key(source, target):
    symbol, family, market, currency, settlement = target
    if source == "PPI":
        return symbol, family, settlement
    if source == "DATA912":
        return symbol, family
    if source in {"IOL", "IOL_CONNECTOR"}:
        return symbol, market
    # A3 symbol aliases and BYMA resolution must be reviewed independently.
    return symbol, family, market, settlement


def _day(row, source, observed):
    if source == "IOL_CONNECTOR":
        stamp = row.get("time")
        if type(stamp) not in (int, float):
            raise ValueError("FRESH_CONNECTOR_CLOCK_MISSING")
        source_time = datetime.fromtimestamp(stamp, timezone.utc)
        day = date.fromisoformat(str(row.get("date")))
        if source_time > observed or day != source_time.astimezone(ART).date():
            raise ValueError("FRESH_CONNECTOR_CLOCK_CONFLICT")
        return day
    field = {"PPI": "date", "IOL": "fechaHora", "DATA912": "date",
             "A3_CEM": "dateTime", "BYMA": "date"}[source]
    value = row.get(field)
    if isinstance(value, str) and len(value) == 10:
        return date.fromisoformat(value)
    source_time = instant(value)
    if source_time > observed:
        raise ValueError("FRESH_PROVIDER_CLOCK_IN_FUTURE")
    return source_time.astimezone(ART).date()


def _rows(payload, source):
    if isinstance(payload, list):
        return payload
    if source == "A3_CEM" and isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return payload["data"]
    if source == "IOL" and isinstance(payload, dict) and isinstance(payload.get("precios"), list):
        return payload["precios"]
    raise ValueError("FRESH_PROVIDER_SHAPE_UNVERIFIED")


def _explicit_currency(row):
    values = [row[name] for name in ("currency", "quoteCurrency", "moneda")
              if name in row]
    if not values:
        return None
    try:
        normalized = {cash_currency(value) for value in values}
    except (ValueError, TypeError):
        return "UNVERIFIED"
    return normalized.pop() if len(normalized) == 1 else "UNVERIFIED"


def _identity_conflicts(row, target):
    for names, expected in ((('symbol', 'ticker'), target[0]),
                            (('instrumentType', 'type'), target[1]),
                            (('market',), target[2]),
                            (('settlementTerm',), target[4])):
        if any(name in row and str(row[name]).strip().upper() != expected
               for name in names):
            return True
    return False


def _ohlc_range_conflict(row, source):
    """Flag contradictory connector OHLC without rewriting provider evidence."""
    if source != "IOL_CONNECTOR":
        return False
    values = [row.get(field) for field in ("open", "high", "low", "close")]
    if any(type(value) not in (int, float) or not math.isfinite(value)
           for value in values):
        return False
    opening, high, low, close = values
    return low > min(opening, close, high) or high < max(opening, close, low)


def _safe_path(destination, protected_paths):
    path = Path(destination).absolute()
    if path.resolve() != path or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("FRESH_SIDECAR_ALIAS_FORBIDDEN")
    if path.name in {"market_history.db", "observer_v17.db", "trading_system.db"}:
        raise ValueError("FRESH_LEGACY_DESTINATION_FORBIDDEN")
    if path.is_relative_to(Path("/opt/porota-trading")):
        raise ValueError("FRESH_HOST_DESTINATION_FORBIDDEN")
    if any(path == Path(other).absolute().resolve() for other in protected_paths):
        raise ValueError("FRESH_LEGACY_DESTINATION_FORBIDDEN")
    if path.exists():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("FRESH_SIDECAR_ALIAS_FORBIDDEN")
    return path


def preflight(targets, source):
    """Reject ambiguous and unbounded requests before any source login."""
    if source not in SOURCES:
        raise ValueError("FRESH_SOURCE_UNVERIFIED")
    keys, resolved = {}, []
    for raw in targets:
        target = _target(raw)
        if target in resolved:
            raise ValueError("FRESH_DUPLICATE_TARGET")
        key = _request_key(source, target)
        if key in keys:
            raise ValueError("FRESH_PROVIDER_REQUEST_AMBIGUOUS")
        keys[key] = target
        resolved.append(target)
        if len(resolved) > MAX_TARGETS:
            raise ValueError("FRESH_TARGET_BUDGET_EXCEEDED")
    if not resolved:
        raise ValueError("FRESH_NO_TARGETS")
    return resolved


def preflight_destination(destination, protected_paths=()):
    path = _safe_path(destination, protected_paths)
    if path.exists():
        raise ValueError("FRESH_GENERATION_ALREADY_EXISTS")
    return path


def capture(destination, targets, source, fetch, *, now=None, protected_paths=()):
    """Fetch at most 40 exact targets into an isolated, newly created DB.

    ``fetch(target, from_day, to_day)`` is a read-only source adapter. Missing
    rows, unknown schema, conflicting identity and provider exceptions remain
    diagnostic outcomes; no v2 canonical rows or old database are modified.
    """
    if not callable(fetch):
        raise ValueError("FRESH_SOURCE_UNVERIFIED")
    system_now = datetime.now(timezone.utc)
    captured_at = instant(now or system_now)
    if abs((captured_at - system_now).total_seconds()) > MAX_CLOCK_SKEW_SECONDS:
        raise ValueError("FRESH_CLOCK_UNVERIFIED")
    from_day, to_day = daily_start(captured_at), captured_at.astimezone(ART).date()
    resolved = preflight(targets, source)
    path = preflight_destination(destination, protected_paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents replacing any existing history or racing a
    # second capture. The private mode also keeps captured market data out of
    # a public artifact or workflow log.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(descriptor)
    results = []
    with sqlite3.connect(path, timeout=3) as connection:
        connection.execute('PRAGMA foreign_keys=ON')
        connection.executescript(DDL)
        connection.execute("INSERT INTO fresh_generation_rc6 VALUES(1,?,?,?)",
                           (captured_at.isoformat(), from_day.isoformat(), "EVIDENCE_ONLY_365_ART_V1"))
        for target in resolved:
            observed = None
            status, reason, rows, expired, invalid = "QUARANTINED", "", [], 0, 0
            row_evidence = []
            currency_status = "REQUEST_ONLY"
            digest, size = "", 0
            try:
                payload = fetch(target, from_day, to_day)
                observed = datetime.now(timezone.utc)
                raw = _body(payload)
                digest, size = sha256(raw).hexdigest(), len(raw)
                if size > MAX_PAYLOAD_BYTES:
                    raise ValueError("FRESH_RESPONSE_BUDGET_EXCEEDED")
                original_rows = _rows(payload, source)
                if len(original_rows) > MAX_ROWS:
                    raise ValueError("FRESH_ROW_BUDGET_EXCEEDED")
                explicit = 0
                days = {}
                conflict = False
                ohlc_conflict = False
                for provider_index, row in enumerate(original_rows):
                    if not isinstance(row, dict):
                        invalid += 1
                        continue
                    if _identity_conflicts(row, target):
                        invalid += 1
                        continue
                    try:
                        day = _day(row, source, observed)
                    except (ValueError, TypeError, OverflowError):
                        invalid += 1
                        continue
                    if day < from_day:
                        expired += 1
                        continue
                    if day > to_day:
                        invalid += 1
                        continue
                    monetary = _explicit_currency(row)
                    if monetary is not None:
                        if monetary != target[3]:
                            invalid += 1
                            continue
                        explicit += 1
                    signature = _body(row)
                    if day in days and days[day] != signature:
                        conflict = True
                    ohlc_conflict |= _ohlc_range_conflict(row, source)
                    days[day] = signature
                    rows.append(row)
                    row_evidence.append((provider_index, day.isoformat(),
                                         sha256(signature).hexdigest()))
                if rows and explicit == len(rows):
                    currency_status = "EXPLICIT_ROWS"
                reason = ("FRESH_DUPLICATE_DAY_CONFLICT" if conflict else
                          "FRESH_OHLC_RANGE_CONFLICT" if ohlc_conflict else
                          "FRESH_REVIEW_REQUIRED" if rows else
                          "FRESH_NO_VALID_IN_WINDOW_ROWS")
                # Even complete monetary rows cannot prove adjustment basis,
                # settlement-term attribution and their historical PIT clock.
                status = ("STAGED_REVIEW_REQUIRED" if rows and not conflict and
                          not ohlc_conflict else "QUARANTINED")
            except (Exception,) as exc:
                # Never put provider error text, tokens, URL or payload in a
                # public result. A local row records only an allowlisted code.
                reason = str(exc) if str(exc) in {
                    "FRESH_RESPONSE_BUDGET_EXCEEDED", "FRESH_ROW_BUDGET_EXCEEDED",
                    "FRESH_PROVIDER_SHAPE_UNVERIFIED"} else "FRESH_SOURCE_ERROR"
                rows = []
                row_evidence = []
            observed = observed or datetime.now(timezone.utc)
            inserted = connection.execute("""INSERT INTO fresh_captures_rc6(
              source,symbol,instrument_type,market,currency,settlement,
              requested_from,requested_to,first_known_at,canonical_payload_sha256,
              canonical_payload_bytes,retained_rows_json,retained_rows,dropped_expired,
              dropped_invalid,status,reason,provider_currency_status,
              price_basis_status,readiness_implication,execution_allowed)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (source,*target,from_day.isoformat(),to_day.isoformat(),observed.isoformat(),
               digest,size,_body(rows).decode(),len(rows),expired,invalid,status,reason,
               currency_status,"UNVERIFIED","NONE",0))
            connection.executemany("""INSERT INTO fresh_row_revisions_rc6(
              capture_id,retained_ordinal,provider_row_index,economic_day,
              canonical_row_sha256,first_known_at) VALUES(?,?,?,?,?,?)""",
              ((inserted.lastrowid, ordinal, provider_index, day, digest, observed.isoformat())
               for ordinal, (provider_index, day, digest) in enumerate(row_evidence)))
            results.append({"identity": list(target), "status": status, "reason": reason,
                            "retained_rows": len(rows), "dropped_expired": expired,
                            "dropped_invalid": invalid,
                            "canonical_payload_sha256": digest,
                            "provider_currency_status": currency_status,
                            "price_basis_status": "UNVERIFIED"})
    return {"state": "EVIDENCE_ONLY_NO_GO", "source": source,
            "generation_path": str(path), "cutoff_day": from_day.isoformat(),
            "selected": len(resolved), "fetch_invocations": len(resolved),
            "history_v2_writes": 0, "execution_allowed": False,
            "ready_paper_implication": "NONE", "results": results}
