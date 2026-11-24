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

import ct_ppi_history_salvage_hf6 as salvage
import cu_history_store_v2_hf6 as history
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
                if (wrapper.get("symbol"), wrapper.get("instrument_type") or wrapper.get("asset_class"),
                    wrapper.get("market"), wrapper.get("currency")) != (symbol, family, "BYMA", "ARS"):
                    continue
                if wrapper.get("settlement") not in T1 or row["quality"] != "VALID_PAYLOAD":
                    continue
                if (date.fromisoformat(wrapper["date_to"]) < repair.BOOTSTRAP_START or
                    date.fromisoformat(wrapper["date_from"]) > repair.CUTOFF):
                    continue
                observed = history.instant(row["recorded_at"])
                found.append((observed, row, wrapper))
            except (TypeError, ValueError, KeyError, AttributeError):
                result["index_metadata_rejected"] += 1
    result["eligible_manifest_rows"] = len(found)
    return sorted(found, key=lambda item: item[0], reverse=True)[:FRAGMENTS_PER_SYMBOL]


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


def main():
    data, observed = MemoryStore(), MemoryStore()
    assert exact.manifest_path() == RELEASE_DATA / "evidence/ppi_history_exact_v1/manifest_exact_v1.db"
    with closing(readonly(RELEASE_DATA / "market_history.db")) as production:
        production.execute("BEGIN")
        copy_history(production, data)
        previous = production.execute("""SELECT state, targets, archive_imports, ppi_queries, complete, failed
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
    assert data.db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert data.db.execute("PRAGMA foreign_key_check").fetchone() is None
    print(json.dumps({"schema": "rc6.history.ppi-cutoff-pilot.v1", "cutoff": repair.CUTOFF.isoformat(),
        "captured_at": datetime.now(timezone.utc).isoformat(), "provider_requests": 0, "production_sql_writes": 0,
        "store": "MEMORY_COPY_OF_LIVE_HISTORICAL_COHORT", "previous_cutoff_receipt": control,
        "before": before, "after": cohort(data.db), "per_symbol": per_symbol, "quick_check": "ok",
        "provider_contracts_reviewed": len(salvage.VERIFIED_PPI_HISTORY_CONTRACTS)}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("PILOT_ERROR:" + type(error).__name__)
        raise SystemExit(2)
