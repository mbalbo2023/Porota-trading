"""Rehearse the existing PPI ingest with a bounded copy of real HISTORY rows."""
from collections import Counter
from contextlib import closing
from datetime import date, datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import stat
import time

from ak_byma_t1_source_clock import estado_negociacion_historica_byma
from bs_instrument_contracts import cash_currency
import ba_data912_history as data912
import ct_ppi_history_salvage_hf6 as salvage
import cu_history_store_v2_hf6 as history
from cw_data912_history_v2_sink import _candles as reference_candles
import fb_raw_evidence_exact_v1 as exact
from scripts import rc6_history_cutoff_repair_once as repair


RELEASE_DATA = Path("/app/data")
SYMBOLS = {"GGAL": "ACCIONES", "AAPL": "CEDEARS"}
TABLES = ("history_versions_v2", "history_canonical_v2")
ROWS_PER_PREFIX = 512
FRAGMENTS_PER_SYMBOL = 12
T1 = {"A-24HS", "24HS", "T1", "T+1"}


def readonly(path):
    assert path.resolve(strict=True) == path and stat.S_ISREG(path.lstat().st_mode)
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=.25)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    deadline = time.monotonic() + 3
    conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    return conn


class MemoryStore:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")

    def connect(self):
        return self.db


def copy_history(source, destination):
    history.init_schema(destination)
    with destination.connect() as target:
        for table in TABLES:
            columns = [row[1] for row in target.execute(f"PRAGMA table_info({table})")]
            projection = ",".join(columns)
            marks = ",".join("?" for _ in columns)
            for symbol, family in SYMBOLS.items():
                rows = source.execute(f"""SELECT {projection} FROM {table}
                    WHERE symbol=? AND instrument_type=? AND date BETWEEN ? AND ? LIMIT 4097""",
                    (symbol, family, repair.BOOTSTRAP_START.isoformat(), repair.CUTOFF.isoformat())).fetchall()
                assert len(rows) <= 4096
                target.executemany(f"INSERT INTO {table} ({projection}) VALUES ({marks})", rows)
        assert target.execute("PRAGMA foreign_key_check").fetchone() is None


def object_payload(row, wrapper):
    size = row["body_bytes"]
    digest = row["inner_sha256"]
    if (type(size) is not int or not 0 < size <= repair.LEGACY_ARCHIVE_MAX_BODY_BYTES
            or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)):
        raise ValueError("ARCHIVE_MANIFEST_INVALID")
    path = exact.object_path(digest)
    info = path.lstat()
    if (path.resolve(strict=True) != path or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or not 0 < info.st_size <= repair.LEGACY_ARCHIVE_MAX_BODY_BYTES):
        raise ValueError("ARCHIVE_OBJECT_INVALID")
    with gzip.open(path, "rb") as source:
        payload = source.read(size + 1)
    if len(payload) >= size or hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError("ARCHIVE_BYTES_UNVERIFIED")
    encoded = exact.canonical({**wrapper, "payload_json": payload.decode("utf-8")}).encode("utf-8")
    if (len(encoded) != size or hashlib.sha256(encoded).hexdigest() != row["body_sha256"]
            or row["body_sha256"] != row["body_hash"]):
        raise ValueError("ARCHIVE_WRAPPER_UNVERIFIED")
    bars = json.loads(payload)
    if not isinstance(bars, list) or len(bars) > 4096:
        raise ValueError("ARCHIVE_BARS_INVALID")
    return bars


def fragments_for(manifest, symbol, family, result):
    found = []
    reasons = Counter()
    market_labels = Counter()
    incomplete = []
    nearest = None
    for prefix in (f'[["{symbol}",', f'["{symbol}",'):
        rows = manifest.execute("""SELECT body_bytes, recorded_at, quality,
            CASE WHEN length(inner_sha256)=64 THEN inner_sha256 END AS inner_sha256,
            CASE WHEN length(body_hash)=64 THEN body_hash END AS body_hash,
            CASE WHEN length(body_sha256)=64 THEN body_sha256 END AS body_sha256,
            CASE WHEN length(CAST(wrapper_json AS BLOB))<=16384 THEN wrapper_json END AS wrapper_json
            FROM exact_attempts_v1 WHERE origin='PPI_HISTORY' AND row_key>=? AND row_key<?
            ORDER BY row_key LIMIT ?""", (prefix, prefix + "\uffff", ROWS_PER_PREFIX + 1)).fetchall()
        result["indexed_rows_seen"] += len(rows[:ROWS_PER_PREFIX])
        result["index_truncated"] |= len(rows) > ROWS_PER_PREFIX
        for row in rows[:ROWS_PER_PREFIX]:
            try:
                wrapper = json.loads(row["wrapper_json"])
                first = date.fromisoformat(wrapper["date_from"])
                last = date.fromisoformat(wrapper["date_to"])
                observed = history.instant(row["recorded_at"])
                if wrapper.get("symbol") != symbol:
                    reasons["other_symbol"] += 1
                    continue
                if (wrapper.get("instrument_type") or wrapper.get("asset_class")) != family:
                    reasons["other_family"] += 1
                    continue
                if wrapper.get("market") != "BYMA":
                    reasons["market_unverified"] += 1
                    declared = wrapper.get("market") or wrapper.get("exchange")
                    market_labels[str(declared) if declared in ("BCBA", "MERV", "MERVAL", "UNKNOWN", None)
                                  else "OTHER"] += 1
                    if (row["quality"] == "VALID_PAYLOAD" and first <= repair.CUTOFF and
                            last >= repair.BOOTSTRAP_START):
                        incomplete.append((observed, row, wrapper, first, last))
                    continue
                try:
                    currency = cash_currency(wrapper.get("currency"))
                except (TypeError, ValueError):
                    currency = None
                if currency != "ARS":
                    reasons["currency_unverified_or_other"] += 1
                    continue
                if wrapper.get("settlement") not in T1:
                    reasons["settlement_unverified_or_other"] += 1
                    continue
                if row["quality"] != "VALID_PAYLOAD":
                    reasons["quality_unverified"] += 1
                    continue
                if last < repair.BOOTSTRAP_START or first > repair.CUTOFF:
                    reasons["outside_cutoff"] += 1
                    distance = min(abs((edge - repair.CUTOFF).days) for edge in (first, last))
                    if nearest is None or distance < nearest[0]:
                        nearest = (distance, observed, row, wrapper, first, last)
                    continue
                found.append((observed, row, wrapper))
            except (TypeError, ValueError, KeyError, AttributeError):
                result["index_metadata_rejected"] += 1
    result["eligible_manifest_rows"] = len(found)
    result["out_of_scope_by_reason"] = dict(reasons)
    result["market_labels_without_exact_match"] = dict(market_labels)
    result["market_unverified_overlapping_cutoff"] = len(incomplete)
    result["market_unverified_samples"] = [describe_fragment(*item) for item in
        sorted(incomplete, key=lambda item: item[0], reverse=True)[:2]]
    if nearest is not None and not found:
        _, observed, row, wrapper, first, last = nearest
        result["nearest_other_window"] = describe_fragment(observed, row, wrapper, first, last)
    return sorted(found, key=lambda item: item[0], reverse=True)[:FRAGMENTS_PER_SYMBOL]


def describe_fragment(observed, row, wrapper, first, last):
    sampled = {"request_from": first.isoformat(), "request_to": last.isoformat(),
               "captured_at": observed.isoformat(), "body_integrity": "NO_VERIFICADO"}
    try:
        bars = object_payload(row, wrapper)
        sampled.update(body_integrity="VALID", bars=len(bars),
                       provider_fields=sorted({key for bar in bars if isinstance(bar, dict)
                           for key in bar if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,59}", key)}))
    except (OSError, EOFError, ValueError, UnicodeError):
        pass
    return sampled


def rehearse(manifest, observed, data, symbol, family):
    tally = Counter()
    result = {"indexed_rows_seen": 0, "index_truncated": False, "index_metadata_rejected": 0}
    days = set()
    for captured, row, wrapper in fragments_for(manifest, symbol, family, result):
        try:
            bars = object_payload(row, wrapper)
        except (OSError, EOFError, ValueError, UnicodeError):
            tally["objects_failed_integrity"] += 1
            continue
        tally["objects_verified"] += 1
        bars = [bar for bar in bars if (day := repair._market_day(bar)) is not None
                and repair.BOOTSTRAP_START <= day <= repair.CUTOFF]
        days.update(repair._market_day(bar) for bar in bars)
        tally["bars_in_cutoff"] += len(bars)
        tally["bars_with_currency"] += sum(any(bar.get(name) is not None
            for name in ("currency", "quoteCurrency", "moneda")) for bar in bars)
        tally["bars_with_raw_basis"] += sum(bar.get("priceBasis") == "RAW" and
            bar.get("adjustmentBasis") == "RAW_NO_ADJUSTMENT" and bar.get("adjusted") is False for bar in bars)
        contract = wrapper.get("provider_contract_id") if repair._archive_identity(wrapper) else None
        call = dict(symbol=symbol, instrument_type=family, market="BYMA", currency="ARS",
                    settlement=wrapper["settlement"], payload=bars, requested_from=repair.BOOTSTRAP_START,
                    requested_to=repair.CUTOFF, attempted_at=captured, history_store=data,
                    provider_contract_id=contract)
        applied = salvage.ingest_ppi_payload(observed, **call)
        retried = salvage.ingest_ppi_payload(observed, **call)
        for member in ("valid_rows", "rejected_rows", "canonical_updates", "close_only_rows"):
            tally[member] += applied[member]
        tally["new_versions_on_retry"] += retried["versions_appended"]
        tally["canonical_updates_on_retry"] += retried["canonical_updates"]
    result.update({key: tally[key] for key in (
        "objects_verified", "objects_failed_integrity", "bars_in_cutoff", "bars_with_currency",
        "bars_with_raw_basis", "valid_rows", "rejected_rows", "canonical_updates", "close_only_rows",
        "new_versions_on_retry", "canonical_updates_on_retry")})
    result.update(distinct_days=len(days), first_day=min(days).isoformat() if days else None,
                  last_day=max(days).isoformat() if days else None)
    return result


def cohort(db):
    rows = db.execute("""SELECT symbol, source, price_basis, market,
        currency, settlement, COUNT(*) AS rows, MIN(date) AS first_day, MAX(date) AS last_day
        FROM history_canonical_v2 GROUP BY symbol, source, price_basis, market, currency, settlement
        ORDER BY symbol, source, price_basis, market, currency, settlement""")
    result = [dict(row) for row in rows]
    for item in result:
        for key in ("symbol", "source", "price_basis", "market", "currency", "settlement"):
            if not re.fullmatch(r"[A-Za-z0-9_./+-]{1,70}", item[key]):
                item[key] = "NO_VERIFICADO"
    return result


def reference_gaps(db, symbol, family):
    trading_dates = (date.fromordinal(ordinal) for ordinal in range(
        repair.BOOTSTRAP_START.toordinal(), repair.CUTOFF.toordinal() + 1))
    expected = {day.isoformat() for day in trading_dates if estado_negociacion_historica_byma(
        day, market="BYMA", family=family, settlement="A-24HS") in {"REGULAR", "LIMITED_T1_SOURCE_ONLY"}}
    observed = {row[0] for row in db.execute("""SELECT date FROM history_canonical_v2
        WHERE symbol=? AND instrument_type=? AND market='BCBA' AND source='IOL_MCP'
          AND price_basis='SOURCE_REPORTED'""", (symbol, family))}
    return {"t1_calendar_days": len(expected), "reference_days_on_calendar": len(observed & expected),
            "dates_missing_from_reference": sorted(expected - observed),
            "reference_dates_outside_calendar": sorted(observed - expected)}


def rehearse_reference(store, dates):
    capture = globals().get("DATA912_CAPTURE", {"status": "NOT_REQUESTED"})
    if capture["status"] != "CAPTURED":
        return {"status": capture["status"]}
    selected = [row for row in capture["bars"] if str(row.get("date", ""))[:10] in dates]
    known = history.instant(capture["captured_at"])
    rows, rejected = data912._normalize(selected, repair.BOOTSTRAP_START.isoformat(),
                                        as_of=known, return_rejections=True)
    candles = reference_candles(("AAPL", "CEDEARS", "BYMA", "ARS", "A-24HS"), rows, known.isoformat())
    store.source_reported_only = True
    applied = history.append_many(store, candles)
    retried = history.append_many(store, candles)
    return {"status": "REFERENCE_PILOT", "dates_received": [str(row.get("date", ""))[:10] for row in selected],
            "accepted_dates": [row[0] for row in rows], "rejected": rejected,
            "canonical_updates_in_copy": applied["canonical_updates"],
            "canonical_updates_on_retry": retried["canonical_updates"],
            "versions_on_retry": retried["versions_appended"],
            "response_sha256": capture["response_sha256"], "captured_at": known.isoformat()}


def main():
    data, observed = MemoryStore(), MemoryStore()
    assert exact.manifest_path() == RELEASE_DATA / "evidence/ppi_history_exact_v1/manifest_exact_v1.db"
    with closing(readonly(RELEASE_DATA / "market_history.db")) as production:
        production.execute("BEGIN")
        copy_history(production, data)
        previous = production.execute("""SELECT state, targets, archive_imports, ppi_queries, complete, failed, updated_at
            FROM rc6_history_cutoff_repair_runs WHERE cutoff=?""", (repair.CUTOFF.isoformat(),)).fetchone()
        control = dict(previous) if previous else None
    before = cohort(data.db)
    with closing(readonly(exact.manifest_path())) as manifest:
        manifest.execute("BEGIN")
        assert repair._safe_physical_archive_order(manifest, "exact_attempts_v1", "rowid")
        per_symbol = {}
        for symbol, family in SYMBOLS.items():
            deadline = time.monotonic() + 3
            manifest.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            per_symbol[symbol] = rehearse(manifest, observed, data, symbol, family)
            per_symbol[symbol]["reference_calendar"] = reference_gaps(data.db, symbol, family)
    reference = rehearse_reference(data, per_symbol["AAPL"]["reference_calendar"]["dates_missing_from_reference"])
    assert data.db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert data.db.execute("PRAGMA foreign_key_check").fetchone() is None
    print(json.dumps({"schema": "rc6.history.ppi-cutoff-pilot.v1", "cutoff": repair.CUTOFF.isoformat(),
        "captured_at": datetime.now(timezone.utc).isoformat(), "provider_requests": 0, "production_sql_writes": 0,
        "store": "MEMORY_COPY_OF_LIVE_HISTORICAL_COHORT", "previous_cutoff_receipt": control,
        "before": before, "after": cohort(data.db), "per_symbol": per_symbol, "quick_check": "ok", "data912": reference,
        "provider_contracts_reviewed": len(salvage.VERIFIED_PPI_HISTORY_CONTRACTS)}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("PILOT_ERROR:" + type(error).__name__)
        raise SystemExit(2)
