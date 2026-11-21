"""One-time, resumable archive repair through the 2026-09-21 cutoff.

The normal path imports only exact PPI history objects already archived for the
matching identity. It never touches orders, IOL, or other instrument families.
Live PPI gap reads are disabled by default and require a deliberate runtime
override after a counted review. Re-running is idempotent.
"""
from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import time
from typing import Any, NamedTuple
from zoneinfo import ZoneInfo

# The one-shot runner is invoked by absolute path from the container.  Keep the
# repository modules importable without depending on Docker's working directory.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from ak_byma_t1_source_clock import estado_negociacion_historica_byma

CUTOFF = date(2026, 9, 21)
# First-history repair is explicitly bounded: it is never an unbounded query.
BOOTSTRAP_START = CUTOFF - timedelta(days=365)
FAMILIES = {"ACCIONES", "CEDEARS"}
LOCK_PATH = Path(os.getenv("HIST_DB_PATH", "/app/data/market_history.db")).parent / ".rc6-history-cutoff-repair.lock"
STATE_TABLE = "rc6_history_cutoff_repair_exact_v2"
RUN_TABLE = "rc6_history_cutoff_repair_runs"
# A COLD legacy archive is evidence, not permission for an unbounded replay.
LEGACY_ARCHIVE_SCAN_LIMIT = 4096
LEGACY_ARCHIVE_MAX_BODY_BYTES = 2 * 1024 * 1024
LEGACY_ARCHIVE_MAX_SCANNED_BYTES = 128 * 1024 * 1024
LEGACY_ARCHIVE_SELECTED_BYTES = 64 * 1024 * 1024
MARKET_TZ = ZoneInfo("America/Argentina/Buenos_Aires")


class ArchiveFragment(NamedTuple):
    rows: list[dict]
    recorded_at: str
    provider_contract_id: str | None


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _history_store():
    from cv_history_store_adapter_hf6 import default_history_store
    return default_history_store()


def _init_state(db) -> None:
    with db.connect() as c:
        c.executescript(f"""
        CREATE TABLE IF NOT EXISTS {STATE_TABLE}(
          cutoff TEXT NOT NULL, symbol TEXT NOT NULL, instrument_type TEXT NOT NULL,
          market TEXT NOT NULL, settlement TEXT NOT NULL, currency TEXT NOT NULL,
          price_basis TEXT NOT NULL, adjustment_basis TEXT NOT NULL, state TEXT NOT NULL,
          source TEXT, requested_from TEXT, requested_to TEXT, valid_rows INTEGER NOT NULL DEFAULT 0,
          latest_date TEXT, error_class TEXT, updated_at TEXT NOT NULL,
          PRIMARY KEY(cutoff,symbol,instrument_type,market,settlement,currency,price_basis,adjustment_basis)
        );
        CREATE TABLE IF NOT EXISTS {RUN_TABLE}(
          cutoff TEXT PRIMARY KEY, state TEXT NOT NULL, targets INTEGER NOT NULL,
          archive_imports INTEGER NOT NULL, ppi_queries INTEGER NOT NULL,
          complete INTEGER NOT NULL, failed INTEGER NOT NULL, updated_at TEXT NOT NULL
        );
        """)


def _targets(observer_store) -> list[tuple[str, ...]]:
    with observer_store.connect() as c:
        rows = c.execute(
            """SELECT DISTINCT ticker,instrument_type,market,settlement,currency
               FROM financial_instrument_catalog
               WHERE status='AVAILABLE'
                 AND upper(instrument_type) IN ('ACCIONES','CEDEARS')
                 AND trim(coalesce(ticker,''))<>''"""
        ).fetchall()
    result = set()
    for symbol, family, market, settlement, currency in rows:
        item = (str(symbol or "").strip().upper(), str(family or "").strip().upper(),
                str(market or "").strip().upper(), str(settlement or "").strip().upper())
        if not all(item) or item[1] not in FAMILIES or item[2] == "UNKNOWN" or item[3] == "UNKNOWN":
            continue
        from bs_instrument_contracts import cash_currency
        try:
            result.add((*item, cash_currency(currency), "RAW", "RAW_NO_ADJUSTMENT"))
        except ValueError:
            continue
    return sorted(result)


def _exact_key(identity):
    from bs_instrument_contracts import cash_currency
    if len(identity) != 7 or any(not isinstance(x, str) or not x.strip() or x == "UNKNOWN" for x in identity):
        raise ValueError("HISTORY_REPAIR_FULL_SERIES_IDENTITY_REQUIRED")
    key = tuple(identity)
    if cash_currency(key[4]) != key[4] or key[5:] != ("RAW", "RAW_NO_ADJUSTMENT"):
        raise ValueError("HISTORY_REPAIR_UNSUPPORTED_BASIS")
    return key


def _live_identity_unambiguous(targets, identity):
    # PPI history accepts symbol/family/settlement, not a currency selector.
    # Two monetary identities behind that endpoint must not receive the same
    # unqualified response with two arbitrarily assigned currencies.
    key=_exact_key(identity)
    return {tuple(t[2:5]) for t in targets if t[:2]==key[:2] and t[3]==key[3]} == {key[2:5]}


def _latest(db, identity) -> str | None:
    key = _exact_key(identity)
    with db.connect() as c:
        row = c.execute(
            """SELECT MAX(date) FROM history_canonical_v2
               WHERE symbol=? AND instrument_type=? AND market=? AND settlement=?
                 AND currency=? AND price_basis=? AND adjustment_basis=?
                 AND date BETWEEN ? AND ?""",
            (*key, BOOTSTRAP_START.isoformat(), CUTOFF.isoformat()),
        ).fetchone()
    return str(row[0])[:10] if row and row[0] else None


def _resume_start(coverage: dict[str, Any], latest: str) -> date:
    # A date awaiting audit is a bounded request hint, not a certified session.
    for field in ("repair_start", "first_missing"):
        if coverage.get(field):
            return date.fromisoformat(coverage[field])
    return date.fromisoformat(latest) + timedelta(days=1)


def _canonical_issue(row, provider_contracts) -> str | None:
    if row[1] != "PPI_PRODUCTION_HISTORY":
        return "NON_PPI_CANONICAL_HISTORY"
    if row[3] != 0:
        return "CANONICAL_RAW_BASIS_UNVERIFIED"
    try:
        metadata = json.loads(row[2])
    except (TypeError, ValueError):
        return "CANONICAL_PROVENANCE_UNVERIFIED"
    if not isinstance(metadata, dict) or metadata.get("history_quality") not in (
            "FULL_OHLC", "FULL_OHLCV"):
        return "CANONICAL_FULL_OHLC_UNVERIFIED"
    contract_id = metadata.get("provider_contract_id")
    if not isinstance(contract_id, str) or contract_id not in provider_contracts:
        return "PPI_PROVIDER_CONTRACT_UNVERIFIED"
    return None


def _canonical_schema_verified(connection) -> bool:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(history_canonical_v2)")}
    return {"symbol", "instrument_type", "market", "settlement", "currency", "price_basis",
            "adjustment_basis", "date", "source", "metadata_json", "adjusted"} <= columns


def _coverage(db, identity) -> dict[str, Any]:
    """Fail closed unless a full, attested baseline through cutoff is proven.

    The first stored candle is NOT evidence of a historical inception date.
    An unaudited day or limited settlement must never be counted as a holiday.
    Canonical rows from IOL/other sources or with no pinned PPI row contract
    cannot certify an exact PPI series merely because dates are present.
    """
    import ct_ppi_history_salvage_hf6 as salvage

    def blocked(reason):
        return {"complete": False, "start": None, "latest": None,
                "expected": None, "present": 0, "missing": None,
                "first_missing": None, "repair_start": BOOTSTRAP_START.isoformat(),
                "attested_days": frozenset(),
                "reason": reason}

    with db.connect() as c:
        try:
            if not _canonical_schema_verified(c):
                return blocked("CANONICAL_PROVENANCE_SCHEMA_UNVERIFIED")
            # Bound SQL work as well as rows and Python heap. Never sort all
            # historical years just to check the 365-day cutoff.
            deadline = time.monotonic() + 2.0
            c.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            rows = []
            metadata_bytes = 0
            try:
                cursor = c.execute(
                    """SELECT substr(date,1,10),source,
                              CASE WHEN length(CAST(metadata_json AS BLOB))<=?
                                   THEN metadata_json END,adjusted
                       FROM history_canonical_v2
                       WHERE symbol=? AND instrument_type=? AND market=? AND settlement=?
                         AND currency=? AND price_basis=? AND adjustment_basis=?
                         AND date>=? AND date<=?
                       LIMIT ?""",
                    (LEGACY_ARCHIVE_MAX_BODY_BYTES, *_exact_key(identity),
                     BOOTSTRAP_START.isoformat(), CUTOFF.isoformat(),
                     LEGACY_ARCHIVE_SCAN_LIMIT + 1))
                for row in cursor:
                    if len(rows) >= LEGACY_ARCHIVE_SCAN_LIMIT:
                        return blocked("CANONICAL_SQL_ROW_BUDGET_EXCEEDED")
                    if type(row[2]) is not str:
                        return blocked("CANONICAL_PROVENANCE_UNVERIFIED")
                    metadata_bytes += len(row[2].encode("utf-8", "surrogatepass"))
                    if metadata_bytes > LEGACY_ARCHIVE_SELECTED_BYTES:
                        return blocked("CANONICAL_SQL_BYTE_BUDGET_EXCEEDED")
                    rows.append(row)
            finally:
                c.set_progress_handler(None, 0)
        except (sqlite3.Error, OSError, TypeError, UnicodeError):
            return blocked("CANONICAL_SQL_READ_UNVERIFIED")
    rows.sort(key=lambda row: str(row[0]))
    days = set()
    attested_days = set()
    issues = []
    for row in rows:
        try:
            day = date.fromisoformat(str(row[0])[:10])
        except (TypeError, ValueError):
            continue
        days.add(day)
        issue = _canonical_issue(row, salvage.VERIFIED_PPI_HISTORY_CONTRACTS)
        if issue is not None:
            issues.append(issue)
        else:
            attested_days.add(day)
    if not days:
        return {"complete": False, "start": None, "latest": None, "expected": 0,
                "present": 0, "missing": 0, "attested_days": frozenset(),
                "reason": "NO_VALID_HISTORY"}
    start, latest = min(days), max(days)
    partial = {"complete": False, "start": start, "latest": latest,
               "expected": None, "present": len(days), "missing": None,
               "first_missing": None, "repair_start": BOOTSTRAP_START.isoformat(),
               "attested_days": frozenset(attested_days)}
    _, family, market, settlement, _, _, _ = _exact_key(identity)
    sessions = {}
    # Always use the explicit cutoff window, independent of the first candle.
    current = BOOTSTRAP_START
    while current <= CUTOFF:
        sessions[current] = estado_negociacion_historica_byma(
            current, market=market, family=family, settlement=settlement)
        current += timedelta(days=1)
    if "UNVERIFIED" in sessions.values():
        return {**partial, "reason": "BASELINE_CALENDAR_UNAUDITED"}
    if issues:
        for reason in ("NON_PPI_CANONICAL_HISTORY", "CANONICAL_RAW_BASIS_UNVERIFIED"):
            if reason in issues:
                return {**partial, "reason": reason}
        return {**partial, "reason": issues[0]}

    if any(sessions.get(day) == "CLOSED" for day in days):
        return {**partial, "reason": "CANONICAL_OUTSIDE_BYMA_SESSION"}
    if "LIMITED_SETTLEMENT_UNVERIFIED" in sessions.values():
        return {**partial, "reason": "BASELINE_SETTLEMENT_CALENDAR_UNVERIFIED"}
    expected_days = [day for day, status in sessions.items()
                     if status in {"REGULAR", "LIMITED_T1_SOURCE_ONLY"}]
    missing = [day for day in expected_days if day not in days]
    complete = latest >= CUTOFF and not missing
    return {"complete": complete, "start": start, "latest": latest,
            "expected": len(expected_days), "present": len(days),
            "missing": len(missing), "attested_days": frozenset(attested_days),
            "first_missing": min(missing).isoformat() if missing else None,
            "reason": "COVERAGE_COMPLETE" if complete else
                      ("CUTOFF_NOT_REACHED" if latest < CUTOFF else "MISSING_BYMA_SESSIONS")}


def _save(db, identity, *, state: str, source: str = "", requested_from: str = "",
          rows: int = 0, latest: str | None = None, error: str | None = None) -> None:
    symbol, family, market, settlement, currency, price_basis, adjustment_basis = _exact_key(identity)
    with db.connect() as c:
        c.execute(
            f"""INSERT INTO {STATE_TABLE}
                (cutoff,symbol,instrument_type,market,settlement,currency,price_basis,adjustment_basis,state,source,
                 requested_from,requested_to,valid_rows,latest_date,error_class,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(cutoff,symbol,instrument_type,market,settlement,currency,price_basis,adjustment_basis) DO UPDATE SET
                 state=excluded.state,source=excluded.source,
                 requested_from=excluded.requested_from,requested_to=excluded.requested_to,
                 valid_rows=excluded.valid_rows,latest_date=excluded.latest_date,
                 error_class=excluded.error_class,updated_at=excluded.updated_at""",
            (CUTOFF.isoformat(), symbol, family, market, settlement, currency, price_basis, adjustment_basis, state, source or None,
             requested_from or None, CUTOFF.isoformat(), int(rows), latest, error, _stamp()),
        )


def _archive_identity(wrapper: dict) -> tuple[str, ...] | None:
    """Require the same explicit seven-key RAW identity from either archive.

    Wrapper labels and SHA attest only byte custody. An absent basis, or
    contradictory catalogue hint, cannot authorize RAW economic attribution.
    """
    from bs_instrument_contracts import cash_currency

    if (not isinstance(wrapper, dict) or wrapper.get("price_basis") != "RAW" or
            wrapper.get("adjustment_basis") != "RAW_NO_ADJUSTMENT" or
            ("adjusted" in wrapper and wrapper["adjusted"] is not False) or
            wrapper.get("authority") not in (None, "RAW_PROVIDER_EVIDENCE_ONLY")):
        return None
    if (wrapper.get("instrument_type") and wrapper.get("asset_class") and
            str(wrapper["instrument_type"]).strip().upper() !=
            str(wrapper["asset_class"]).strip().upper()):
        return None
    metadata = wrapper.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        return None
    metadata = metadata or {}
    if (metadata.get("price_basis") not in (None, "RAW") or
            metadata.get("adjustment_basis") not in (None, "RAW_NO_ADJUSTMENT") or
            ("adjusted" in metadata and metadata["adjusted"] is not False)):
        return None
    try:
        identity = _exact_key((
            str(wrapper["symbol"]).strip().upper(),
            str(wrapper.get("instrument_type") or wrapper["asset_class"]).strip().upper(),
            str(wrapper["market"]).strip().upper(),
            str(wrapper["settlement"]).strip().upper(),
            cash_currency(wrapper["currency"]), "RAW", "RAW_NO_ADJUSTMENT"))
        aliases = (("symbol", 0), ("instrument_type", 1), ("asset_class", 1),
                   ("market", 2), ("exchange", 2), ("settlement", 3))
        if any(str(metadata[name]).strip().upper() != identity[index]
               for name, index in aliases if metadata.get(name) not in (None, "")):
            return None
        if (metadata.get("currency") not in (None, "") and
                cash_currency(metadata["currency"]) != identity[4]):
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return identity


def _safe_physical_archive_order(c, table: str, column: str) -> bool:
    """Only an unshadowed SQLite rowid or INTEGER PRIMARY KEY can order archives."""
    if (table, column) not in {
            ("exact_attempts_v1", "rowid"), ("historical_raw_archive", "id")}:
        return False
    try:
        schema = c.execute(
            """SELECT type,CASE WHEN length(CAST(sql AS BLOB))<=16384
                                THEN sql END
               FROM sqlite_master WHERE name=?""", (table,)).fetchone()
        if (not schema or schema[0] != "table" or
                not isinstance(schema[1], str) or
                "WITHOUT ROWID" in schema[1].upper() or
                "CREATE VIRTUAL TABLE" in schema[1].upper()):
            return False
        info = c.execute(f'PRAGMA table_xinfo("{table}")').fetchall()
        if not info or len(info) > 128:
            return False
        if column == "rowid":
            if any(str(row[1]).casefold() in ("rowid", "_rowid_", "oid")
                   for row in info):
                return False
        elif not any(str(row[1]).casefold() == "id" and
                     str(row[2]).upper() == "INTEGER" and row[5] == 1
                     and row[6] == 0 for row in info):
            return False
        plan = c.execute(
            f'EXPLAIN QUERY PLAN SELECT "{column}" FROM "{table}" '
            f'ORDER BY "{column}" DESC LIMIT 1').fetchall()
        return bool(plan) and not any(
            "TEMP B-TREE" in str(row[3]).upper() for row in plan)
    except (sqlite3.Error, OSError, TypeError, ValueError):
        return False


def _archive_candidate(wrapper, bars, recorded_at, attempted):
    """Select a fragment by the sessions its original bytes can cover."""
    if not isinstance(bars, list) or len(bars) > LEGACY_ARCHIVE_SCAN_LIMIT:
        return None
    observed_day = attempted.astimezone(MARKET_TZ).date()
    days = set()
    for row in bars:
        day = _market_day(row)
        if day is None:
            continue
        if day > observed_day:
            return None
        if BOOTSTRAP_START <= day <= CUTOFF:
            days.add(day)
    if not days:
        return None
    contract_id = wrapper.get("provider_contract_id")
    if not isinstance(contract_id, str):
        contract_id = None
    return frozenset(days), ArchiveFragment(bars, recorded_at, contract_id)


def _remember_fragment(selected, key, item, attempted, size, total_bytes):
    previous = selected.get(key)
    if previous is not None and previous[0] >= attempted:
        return total_bytes
    projected = total_bytes - (previous[2] if previous is not None else 0) + size
    if projected > LEGACY_ARCHIVE_SELECTED_BYTES:
        return total_bytes
    selected[key] = attempted, item, size
    return projected


def _fragments_by_identity(selected):
    result = {}
    for (identity, _, _), (_, fragment, _) in sorted(
            selected.items(), key=lambda entry: entry[1][0], reverse=True):
        result.setdefault(identity, []).append(fragment)
    return result


def _archive_payloads(targets: set[tuple[str, ...]]) -> dict[tuple[str, ...], list[ArchiveFragment]]:
    import fb_raw_evidence_exact_v1 as exact

    path = exact.manifest_path()
    if not path.exists():
        return _legacy_archive_payloads(targets)
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            return _legacy_archive_payloads(targets)
        c = sqlite3.connect(path.resolve(strict=True).as_uri() + "?mode=ro",
                            uri=True, timeout=20)
    except (OSError, sqlite3.Error):
        return _legacy_archive_payloads(targets)

    selected = {}
    scanned_bytes = selected_bytes = 0
    with closing(c):
        c.row_factory = sqlite3.Row
        try:
            c.execute("PRAGMA query_only=ON")
            if not _safe_physical_archive_order(c, "exact_attempts_v1", "rowid"):
                return _legacy_archive_payloads(targets)
            # Return bounded text metadata, never materialize 8MiB TEXT values.
            rows = c.execute(
                """SELECT
                          CASE WHEN length(CAST(recorded_at AS BLOB))<=128
                               THEN recorded_at END AS recorded_at,
                          CASE WHEN length(CAST(inner_sha256 AS BLOB))<=64
                               THEN inner_sha256 END AS inner_sha256,
                          CASE WHEN length(CAST(body_hash AS BLOB))<=64
                               THEN body_hash END AS body_hash,
                          CASE WHEN length(CAST(body_sha256 AS BLOB))<=64
                               THEN body_sha256 END AS body_sha256,
                          body_bytes,
                          CASE WHEN length(CAST(wrapper_json AS BLOB))<=?
                               THEN wrapper_json END AS wrapper_json
                   FROM exact_attempts_v1
                   WHERE rowid IN (SELECT rowid FROM exact_attempts_v1
                                   ORDER BY rowid DESC LIMIT ?)
                     AND origin='PPI_HISTORY' AND quality='VALID_PAYLOAD'
                   ORDER BY rowid DESC""",
                (LEGACY_ARCHIVE_MAX_BODY_BYTES, LEGACY_ARCHIVE_SCAN_LIMIT),
            )
        except sqlite3.Error:
            return _legacy_archive_payloads(targets)
        for row in rows:
            wrapper_json = row["wrapper_json"]
            body_bytes = row["body_bytes"]
            if (not isinstance(wrapper_json, str) or
                    type(body_bytes) is not int or
                    not 0 < body_bytes <= LEGACY_ARCHIVE_MAX_BODY_BYTES):
                continue
            wrapper_size = len(wrapper_json.encode("utf-8", "surrogatepass"))
            scanned_bytes += wrapper_size
            if scanned_bytes > LEGACY_ARCHIVE_MAX_SCANNED_BYTES:
                break
            try:
                attempted = datetime.fromisoformat(str(row["recorded_at"]).replace("Z", "+00:00"))
                if attempted.tzinfo is None:
                    continue
                digest = row["inner_sha256"]
                if (not isinstance(digest, str) or len(digest) != 64 or
                        any(ch not in "0123456789abcdef" for ch in digest)):
                    continue
                object_path = exact.object_path(digest)
                object_stat = object_path.lstat()
                if not stat.S_ISREG(object_stat.st_mode) or object_stat.st_nlink != 1:
                    continue
                # Never call reconstruct() with an unbounded gzip member. A
                # compromised size in SQLite cannot turn the cold read into
                # gigabytes of decompression.
                with gzip.open(object_path, "rb") as stream:
                    inner = stream.read(LEGACY_ARCHIVE_MAX_BODY_BYTES + 1)
                scanned_bytes += len(inner)
                if (scanned_bytes > LEGACY_ARCHIVE_MAX_SCANNED_BYTES or
                        len(inner) > LEGACY_ARCHIVE_MAX_BODY_BYTES or
                        hashlib.sha256(inner).hexdigest() != digest):
                    if scanned_bytes > LEGACY_ARCHIVE_MAX_SCANNED_BYTES:
                        break
                    continue
                wrapper = json.loads(wrapper_json)
                if not isinstance(wrapper, dict) or "payload_json" in wrapper:
                    continue
                wrapper["payload_json"] = inner.decode("utf-8", "surrogatepass")
                encoded = exact.canonical(wrapper).encode("utf-8", "surrogatepass")
                expected_hash = hashlib.sha256(encoded).hexdigest()
                if (len(encoded) != body_bytes or
                        expected_hash != row["body_hash"] or
                        expected_hash != row["body_sha256"]):
                    continue
                identity = _archive_identity(wrapper)
                if identity not in targets:
                    continue
                bars = json.loads(wrapper["payload_json"])
                candidate = _archive_candidate(wrapper, bars, row["recorded_at"], attempted)
                if candidate is None:
                    continue
            except (OSError, EOFError, KeyError, TypeError, ValueError,
                    UnicodeDecodeError, RecursionError):
                continue
            days, fragment = candidate
            selected_bytes = _remember_fragment(
                selected, (identity, days, fragment.provider_contract_id), fragment,
                attempted, body_bytes, selected_bytes)
    # Replay external fragments first. The legacy archive may cover a date
    # missing from those fragments for the same exact series.
    grouped = _fragments_by_identity(selected)
    for identity, fragments in _legacy_archive_payloads(targets).items():
        grouped.setdefault(identity, []).extend(fragments)
    return grouped


def _legacy_archive_payloads(targets) -> dict[tuple[str, ...], list[ArchiveFragment]]:
    """Bounded, hash-verified fallback to existing historical_raw_archive.

    The external manifest may be absent before legacy migration. Reuse already
    archived PPI bytes without contacting the broker or fabricating dates.
    """
    if not targets:
        return {}
    # HistoricalStore.connect() may create an absent DB and opens RW before
    # PRAGMA query_only. Cold archive inspection must open a pre-existing file
    # with SQLite mode=ro from its exact identity path instead.
    path = Path(_history_store().path)
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            return {}
        c = sqlite3.connect(f"{path.resolve(strict=True).as_uri()}?mode=ro",
                            uri=True, timeout=20)
    except (OSError, sqlite3.Error):
        return {}

    selected = {}
    selected_bytes = 0
    scanned_bytes = 0
    with closing(c):
        try:
            c.execute("PRAGMA query_only=ON")
            if not _safe_physical_archive_order(c, "historical_raw_archive", "id"):
                return {}
            rows = c.execute("""SELECT
                  CASE WHEN length(CAST(body_hash AS BLOB))<=64
                       THEN body_hash END AS body_hash,
                  CASE WHEN length(CAST(recorded_at AS BLOB))<=128
                       THEN recorded_at END AS recorded_at,
                  CASE WHEN length(CAST(body_json AS BLOB))<=?
                       THEN body_json END AS body_json
              FROM historical_raw_archive
              WHERE id IN (SELECT id FROM historical_raw_archive
                           ORDER BY id DESC LIMIT ?)
                AND origin='PPI_HISTORY' AND quality='VALID_PAYLOAD'
              ORDER BY id DESC""", (LEGACY_ARCHIVE_MAX_BODY_BYTES,
                                    LEGACY_ARCHIVE_SCAN_LIMIT))
        except sqlite3.Error:
            return {}
        for expected_hash, recorded_at, raw in rows:
            if not isinstance(raw, str):
                continue
            # The query is streamed, but even a run of rejected oversized
            # wrappers must not turn one-shot recovery into a full DB sweep.
            if len(raw) > LEGACY_ARCHIVE_MAX_BODY_BYTES:
                scanned_bytes += len(raw)
                if scanned_bytes > LEGACY_ARCHIVE_MAX_SCANNED_BYTES:
                    break
                continue
            encoded = raw.encode("utf-8")
            scanned_bytes += len(encoded)
            if scanned_bytes > LEGACY_ARCHIVE_MAX_SCANNED_BYTES:
                break
            if (len(encoded) > LEGACY_ARCHIVE_MAX_BODY_BYTES or
                    not isinstance(recorded_at, str) or
                    hashlib.sha256(encoded).hexdigest() != expected_hash):
                continue
            try:
                attempted = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
                wrapper = json.loads(raw)
                if attempted.tzinfo is None or not isinstance(wrapper, dict):
                    continue
                identity = _archive_identity(wrapper)
                if identity not in targets:
                    continue
                bars = json.loads(wrapper.get("payload_json") or "[]")
                candidate = _archive_candidate(wrapper, bars, recorded_at, attempted)
                if candidate is None:
                    continue
            except (KeyError, TypeError, ValueError, RecursionError):
                continue
            days, fragment = candidate
            selected_bytes = _remember_fragment(
                selected, (identity, days, fragment.provider_contract_id), fragment,
                attempted, len(encoded), selected_bytes)
    return _fragments_by_identity(selected)


def _market_day(row) -> date | None:
    from cp_history_ingest_policy_hf6 import _dt

    if not isinstance(row, dict):
        return None
    try:
        return _dt(row.get("date")).astimezone(MARKET_TZ).date()
    except (ValueError, OverflowError):
        return None


def _filtered(payload: Any, start: date, *, already_attested=frozenset()) -> list[dict]:
    if not isinstance(payload, list):
        return []
    return [row for row in payload
            if (day := _market_day(row)) is not None and start <= day <= CUTOFF
            and day not in already_attested]


def _record_exact(observer_store, identity, payload, start: date, attempted_at: str) -> None:
    import bl_candle_engine as canonical_json
    import bf_production_paper_observer as observer
    import fb_raw_evidence_exact_v1 as exact
    import cp_history_ingest_policy_hf6 as policy

    symbol, family, _market, settlement, currency, _, _ = _exact_key(identity)
    metadata = observer.financial_catalog.lookup(observer_store, symbol, family, settlement)
    valid = policy.validate_provider_history(
        _filtered(payload, start), as_of=datetime.fromisoformat(attempted_at.replace("Z", "+00:00")),
        date_from=start, date_to=CUTOFF,
    ).valid_count
    quality = "VALID_PAYLOAD" if valid else "EMPTY_OR_INVALID"
    wrapper = {
        "symbol": symbol, "asset_class": family, "settlement": settlement,
        "market": _market, "currency": currency, "price_basis": "RAW",
        "adjustment_basis": "RAW_NO_ADJUSTMENT",
        "date_from": start.isoformat(), "date_to": CUTOFF.isoformat(),
        "metadata": metadata, "valid_rows": valid,
        "payload_json": json.dumps(payload, ensure_ascii=False, default=str),
    }
    row_key = canonical_json.canonical([*_exact_key(identity), attempted_at])
    exact.archive_wrapper(row_key=row_key, wrapper=wrapper, recorded_at=attempted_at, quality=quality)


def _ingest(observer_store, history_store, identity, payload, start: date, *, source: str,
            observed_at: str, provider_contract_id: str | None = None,
            already_attested=frozenset()) -> tuple[int, str | None]:
    import ct_ppi_history_salvage_hf6 as salvage

    bounded = _filtered(payload, start, already_attested=already_attested)
    if not bounded:
        return 0, None
    result = salvage.ingest_ppi_payload(
        observer_store, symbol=identity[0], instrument_type=identity[1],
        market=identity[2], settlement=identity[3], currency=_exact_key(identity)[4], payload=bounded,
        requested_from=start, requested_to=CUTOFF, attempted_at=observed_at,
        history_store=history_store, provider_contract_id=provider_contract_id,
    )
    return int(result.get("valid_rows") or 0), result.get("last_date")


def _control(db, *, state: str, targets: int, archive: int, ppi: int, complete: int, failed: int) -> None:
    with db.connect() as c:
        c.execute(
            f"""INSERT INTO {RUN_TABLE}
                (cutoff,state,targets,archive_imports,ppi_queries,complete,failed,updated_at)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(cutoff) DO UPDATE SET state=excluded.state,targets=excluded.targets,
                 archive_imports=excluded.archive_imports,ppi_queries=excluded.ppi_queries,
                 complete=excluded.complete,failed=excluded.failed,updated_at=excluded.updated_at""",
            (CUTOFF.isoformat(), state, targets, archive, ppi, complete, failed, _stamp()),
        )


def main(*, shared_reader=None, reserve_history_get=None) -> int:
    """Archive-first repair; live history requires a borrowed scanner session.

    CLI execution never opens its own PPI connection. The future in-process
    caller must supply both its already-authenticated reader and a per-GET
    reservation callback; no such caller is wired into the runtime today.
    """
    from cg_paper_workspace import runtime_store
    import bf_production_paper_observer as observer

    history_store = _history_store()
    # The one-shot writer requires the exact financial identity columns. The
    # physically restored legacy store lacks them and belongs to copy migration.
    try:
        with history_store.connect() as connection:
            schema_verified = _canonical_schema_verified(connection)
    except (sqlite3.Error, OSError, ValueError):
        print("RC6_HISTORY_CUTOFF_REPAIR=BLOCKED_CANONICAL_SQL_READ_UNVERIFIED")
        return 2
    if not schema_verified:
        print("RC6_HISTORY_CUTOFF_REPAIR=BLOCKED_CANONICAL_PROVENANCE_SCHEMA_UNVERIFIED")
        return 2
    observer_store = runtime_store()
    targets_list = _targets(observer_store)
    if not targets_list:
        print("RC6_HISTORY_CUTOFF_REPAIR=BLOCKED_EMPTY_OR_INVALID_OPERATIONAL_CATALOG")
        return 2
    targets = set(targets_list)
    lock_path = LOCK_PATH
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("RC6_HISTORY_CUTOFF_REPAIR=BLOCKED_LOCK_HELD")
            return 3
        _init_state(history_store)

        with history_store.connect() as c:
            c.execute(
                f"""INSERT INTO {RUN_TABLE}(cutoff,state,targets,archive_imports,ppi_queries,complete,failed,updated_at)
                    VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(cutoff) DO UPDATE SET
                    state='RUNNING',targets=excluded.targets,updated_at=excluded.updated_at""",
                (CUTOFF.isoformat(), "RUNNING", len(targets), 0, 0, 0, len(targets), _stamp()),
            )

        completed = set()
        archives = _archive_payloads(targets)
        archive_count = ppi_count = failed = 0
        for identity in targets_list:
            latest = _latest(history_store, identity)
            coverage = _coverage(history_store, identity)
            if coverage["complete"]:
                _save(history_store, identity, state="ALREADY_COVERED",
                      source="PPI_CANONICAL", rows=coverage["present"], latest=latest)
                completed.add(identity)
                continue
            archived = archives.get(identity, ())
            if latest:
                # An unaudited baseline has no known first missing *session*.
                # Retry archived evidence from the bounded bootstrap target,
                # even when an isolated existing candle lies on cutoff day.
                # Otherwise resume from the earliest proven missing session.
                start = _resume_start(coverage, latest)
            elif archived:
                start = min((_market_day(row) for fragment in archived
                             for row in _filtered(fragment.rows, BOOTSTRAP_START)),
                            default=None)
            else:
                start = None
            if start is None:
                _save(history_store, identity, state="BLOCKED_NO_CANONICAL_BASELINE",
                      source="PPI_ARCHIVE_OR_CANONICAL")
                failed += 1
                continue
            if start > CUTOFF:
                _save(history_store, identity, state="COVERAGE_GAPS_BLOCKED",
                      source="PPI_CANONICAL", rows=coverage["present"], latest=latest,
                      error=coverage["reason"])
                failed += 1
                continue
            if archived:
                imported = False
                try:
                    for fragment in archived:
                        rows, _ = _ingest(
                            observer_store, history_store, identity, fragment.rows, start,
                            source="PPI_ARCHIVE", observed_at=fragment.recorded_at,
                            provider_contract_id=fragment.provider_contract_id,
                            already_attested=coverage["attested_days"])
                        if rows:
                            if not imported:
                                archive_count += 1
                                imported = True
                            coverage = _coverage(history_store, identity)
                            if coverage["complete"]:
                                break
                    latest = _latest(history_store, identity)
                    if imported and coverage["complete"]:
                        _save(history_store, identity, state="COMPLETE", source="PPI_ARCHIVE",
                              requested_from=start.isoformat(), rows=coverage["present"], latest=latest)
                        completed.add(identity)
                        continue
                    _save(history_store, identity, state="ARCHIVE_PARTIAL_COVERAGE",
                          source="PPI_ARCHIVE", requested_from=start.isoformat(),
                          rows=coverage["present"], latest=latest, error=coverage["reason"])
                except Exception as exc:
                    _save(history_store, identity, state="ARCHIVE_REJECTED", source="PPI_ARCHIVE",
                          requested_from=start.isoformat(), error=type(exc).__name__)
        reader = None
        live_gap_repair = os.getenv("RC6_HISTORY_ALLOW_PPI_GAP_REPAIR", "").strip() == "APPROVED"
        if (len(completed) < len(targets_list) and live_gap_repair and
                (not callable(getattr(shared_reader, "history", None)) or
                 not callable(reserve_history_get))):
            print("RC6_HISTORY_PPI_GAP=BLOCKED_NO_SHARED_SCANNER_SESSION_OR_BUDGET")
        elif len(completed) < len(targets_list) and live_gap_repair and any(
                _live_identity_unambiguous(targets_list,key) for key in targets_list if key not in completed):
            reader = shared_reader

        if len(completed) < len(targets_list) and reader is not None:
            try:
                for index, identity in enumerate(targets_list, 1):
                    if identity in completed:
                        continue
                    if not _live_identity_unambiguous(targets_list,identity):
                        _save(history_store,identity,state="BLOCKED_AMBIGUOUS_PROVIDER_IDENTITY",
                              source="PPI",error="PPI_HISTORY_ENDPOINT_IDENTITY_AMBIGUOUS")
                        failed+=1
                        continue
                    latest = _latest(history_store, identity)
                    coverage = _coverage(history_store, identity)
                    if not latest:
                        # The caller explicitly approved this one-time, bounded
                        # bootstrap through the cutoff; it cannot expand beyond 365 days.
                        start = BOOTSTRAP_START
                    else:
                        start = _resume_start(coverage, latest)
                    if start > CUTOFF:
                        coverage = _coverage(history_store, identity)
                        if coverage["complete"]:
                            _save(history_store, identity, state="ALREADY_COVERED",
                                  source="PPI_CANONICAL", rows=coverage["present"], latest=latest)
                            completed.add(identity)
                        else:
                            _save(history_store, identity, state="COVERAGE_GAPS_BLOCKED",
                                  source="PPI_CANONICAL", rows=coverage["present"], latest=latest,
                                  error=coverage["reason"])
                        continue
                    attempted_at = observer.now_iso()
                    try:
                        payload = None
                        # PPI can transiently return a non-JSON gateway body. Retry
                        # only that transport symptom, bounded and without writing
                        # evidence until a valid response is obtained.
                        for json_attempt in range(3):
                            try:
                                if reserve_history_get(identity, start, CUTOFF) is not True:
                                    raise PermissionError("PPI_HISTORY_GET_BUDGET_DENIED")
                                ppi_count += 1
                                payload = reader.history(identity[0], identity[1], identity[3], start, CUTOFF)
                                break
                            except json.JSONDecodeError:
                                if json_attempt == 2:
                                    raise
                                time.sleep(2 ** json_attempt)
                        _record_exact(observer_store, identity, payload, start, attempted_at)
                        rows, _ = _ingest(observer_store, history_store, identity, payload, start,
                                          source="PPI", observed_at=attempted_at,
                                          already_attested=coverage["attested_days"])
                        latest = _latest(history_store, identity)
                        coverage = _coverage(history_store, identity)
                        if rows and coverage["complete"]:
                            _save(history_store, identity, state="COMPLETE", source="PPI",
                                  requested_from=start.isoformat(), rows=coverage["present"], latest=latest)
                            completed.add(identity)
                        else:
                            _save(history_store, identity,
                                  state="PPI_PARTIAL_COVERAGE" if rows else "NO_NEW_VALID_ROWS",
                                  source="PPI", requested_from=start.isoformat(),
                                  rows=coverage["present"], latest=latest, error=coverage["reason"])
                            failed += 1
                    except Exception as exc:
                        _save(history_store, identity, state="PPI_QUERY_FAILED", source="PPI",
                              requested_from=start.isoformat(), error=type(exc).__name__)
                        failed += 1
                    if index % 20 == 0:
                        _control(history_store, state="RUNNING", targets=len(targets), archive=archive_count,
                                 ppi=ppi_count, complete=len(completed), failed=failed)
                        print(f"RC6_HISTORY_CUTOFF_PROGRESS={index}/{len(targets_list)} COMPLETE={len(completed)} FAILED={failed}", flush=True)
                    time.sleep(max(0.0, float(os.getenv("RC6_HISTORY_REQUEST_PAUSE_SECONDS", "1.0"))))
            finally:
                # The scanner retains sole ownership of authentication and
                # cleanup. An archive repair must never close its live reader.
                reader = None

        complete = len(completed)
        unresolved = [identity for identity in targets_list if identity not in completed]
        quarantined = 0
        if unresolved and os.getenv("RC6_HISTORY_QUARANTINE_REPEATED_PPI", "").strip() == "APPROVED":
            # A flag is not a physical record of repeated provider failure.
            # Prior code would silently mark every unresolved identity stale
            # and the cutoff COMPLETE even when PPI queries were zero.
            # Hold until a separate durable per-identity failure contract is
            # implemented and tested; never self-certify by quarantine.
            print("RC6_HISTORY_QUARANTINE=BLOCKED_REPEATED_PPI_EVIDENCE_UNVERIFIED")
        failed = len(unresolved)
        if failed:
            for identity in unresolved:
                coverage = _coverage(history_store, identity)
                print(
                    "RC6_HISTORY_CUTOFF_INCOMPLETE "
                    f"SYMBOL={identity[0]} FAMILY={identity[1]} SETTLEMENT={identity[3]} "
                    f"REASON={coverage['reason']} FIRST_MISSING={coverage.get('first_missing')} "
                    f"LATEST={coverage['latest']} MISSING={coverage['missing']}",
                    flush=True,
                )
        state = "COMPLETE" if failed == 0 else "PARTIAL"
        effective_targets = len(targets) - quarantined
        _control(history_store, state=state, targets=effective_targets, archive=archive_count,
                 ppi=ppi_count, complete=complete, failed=failed)
        print(f"RC6_HISTORY_CUTOFF_REPAIR={state} TARGETS={effective_targets} ARCHIVE={archive_count} PPI={ppi_count} COMPLETE={complete} FAILED={failed} QUARANTINED={quarantined}")
        return 0 if state == "COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
