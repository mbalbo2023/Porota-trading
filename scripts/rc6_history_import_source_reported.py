"""Import a checked provider capture into History V2 as reference prices."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import date, datetime, timezone
from hashlib import file_digest, sha256
import json
import os
from pathlib import Path
import re
import sqlite3
from zipfile import ZipFile

from cu_history_store_v2_hf6 import (
    ART, SOURCE_REPORTED, Candle, append_many, instant, source_reported_adjustment,
    upgrade_existing_history, validate_daily_values,
)
from cv_history_store_adapter_hf6 import HistoricalStore, SOURCE_REPORTED_APPLICATION_ID
from cp_history_ingest_policy_hf6 import OPERATIONAL_HISTORY_FAMILIES, LEGACY_HISTORY_FAMILIES

MEMBER = "respuestas-mcp.jsonl"
SOURCE = "IOL_MCP"
INTERVALORES = "BYMA_INTERVALORES"
INTERVALORES_MEMBER = "respuestas-intervalores.jsonl"
FAMILIES = OPERATIONAL_HISTORY_FAMILIES | LEGACY_HISTORY_FAMILIES
INTERVALORES_FAMILIES = {"ACCIONES", "CEDEARS"}
MAX_BYTES = 64 * 1024 * 1024
MAX_CAPTURE_ROWS = 1000


def _capture_sha(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _source_store(destination):
    path = Path(os.path.abspath(destination))
    if path.resolve() != path:
        raise ValueError("HISTORY_REFERENCE_PATH_ALIAS")
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        info = path.lstat()
        if not path.is_file() or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("HISTORY_REFERENCE_DESTINATION_UNSAFE") from None
    else:
        os.close(descriptor)
        with closing(sqlite3.connect(path)) as connection:
            connection.execute(f"PRAGMA application_id={SOURCE_REPORTED_APPLICATION_ID}")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        if connection.execute("PRAGMA application_id").fetchone()[0] != SOURCE_REPORTED_APPLICATION_ID:
            raise ValueError("HISTORY_REFERENCE_DESTINATION_REQUIRED")
    return HistoricalStore(str(path), source_reported_only=True)


def _window(capture, observed_at):
    start = date.fromisoformat(capture["from_date"])
    end = date.fromisoformat(capture["to_date"])
    if not 0 <= (end - start).days <= 365 or end > observed_at.astimezone(ART).date():
        raise ValueError("HISTORY_CAPTURE_WINDOW_INVALID")
    if observed_at > datetime.now(timezone.utc):
        raise ValueError("HISTORY_CAPTURE_OBSERVATION_IN_FUTURE")
    return start, end


def _candles(capture, archive_sha, *, source=SOURCE):
    if capture["market"] != "BCBA" or capture["family"] not in FAMILIES:
        raise ValueError("HISTORY_CAPTURE_MARKET_OR_FAMILY_INVALID")
    symbol = capture["symbol"]
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{0,39}", symbol):
        raise ValueError("HISTORY_CAPTURE_SYMBOL_INVALID")
    provenance = {}
    if source == INTERVALORES:
        if capture["family"] not in INTERVALORES_FAMILIES:
            raise ValueError("HISTORY_CAPTURE_INTERVALORES_FAMILY_UNVERIFIED")
        url = f"https://www.intervaloresgroup.com/Bursatil/Especie/{symbol}/MERVAL/24hs."
        if (capture.get("source_url") != url or capture.get("quoted_currency") != "ARS"
                or capture.get("quoted_settlement") != "A-24HS"):
            raise ValueError("HISTORY_CAPTURE_INTERVALORES_SERIES_UNVERIFIED")
        provenance = {"source_url": url, "reported_market": "BYMA",
                      "reported_currency": "ARS", "reported_settlement": "A-24HS"}
    observed_at = instant(capture["received_at"])
    start, end = _window(capture, observed_at)
    payload = capture["payload"]
    payload_sha = _capture_sha(payload)
    if payload_sha != capture["payload_sha256"]:
        raise ValueError("HISTORY_CAPTURE_PAYLOAD_DIGEST_MISMATCH")
    rows = payload.get("bars") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) > MAX_CAPTURE_ROWS:
        raise ValueError("HISTORY_CAPTURE_ROWS_INVALID")

    duplicates = Counter(row.get("date") for row in rows
                         if isinstance(row, dict) and isinstance(row.get("date"), str))
    rejected = Counter()
    candles = []
    for row in rows:
        try:
            if not isinstance(row, dict):
                raise ValueError("HISTORY_CAPTURE_ROW_INVALID")
            day = date.fromisoformat(row["date"])
            if day.isoformat() != row["date"] or not start <= day <= end:
                raise ValueError("HISTORY_CAPTURE_ROW_OUTSIDE_WINDOW")
            if duplicates[row["date"]] != 1:
                raise ValueError("HISTORY_CAPTURE_DUPLICATE_DATE")
            provider_at = None
            if source == SOURCE:
                if type(row["time"]) is not int:
                    raise ValueError("HISTORY_CAPTURE_TIMESTAMP_INVALID")
                provider_at = datetime.fromtimestamp(row["time"], tz=timezone.utc)
                if provider_at.astimezone(ART).date() != day or provider_at > observed_at:
                    raise ValueError("HISTORY_CAPTURE_TIMESTAMP_MISMATCH")
            values = validate_daily_values(
                date=row["date"], open=row["open"], high=row["high"], low=row["low"],
                close=row["close"], volume=row["volume"] if source == SOURCE else None,
                known_at=observed_at, market=provenance.get("reported_market", "BCBA"),
                family=capture["family"], settlement=provenance.get("reported_settlement"),
            )
            candles.append(Candle(
                symbol=symbol, instrument_type=capture["family"], market="BCBA",
                currency="UNKNOWN", settlement="UNKNOWN", date=row["date"],
                source=source, price_basis=SOURCE_REPORTED,
                adjustment_basis=source_reported_adjustment(source),
                observed_at=observed_at, provider_at=provider_at, **values,
                metadata={"archive_sha256": archive_sha, "provider_response_sha256": payload_sha,
                          "reported_volume_unit": "UNKNOWN", "readiness_implication": "NONE",
                          **provenance},
            ))
        except (KeyError, OSError, OverflowError, TypeError, ValueError) as error:
            reason = str(error)
            if not re.fullmatch(r"HISTORY_[A-Z_]+", reason):
                reason = "HISTORY_CAPTURE_ROW_INVALID"
            rejected[reason] += 1
    candles.sort(key=lambda candle: candle.date)
    attempt = _capture_sha([source, symbol, capture["family"], archive_sha, payload_sha])
    return candles, rejected, len(rows), attempt


def import_reference_export(archive, destination, expected_sha256, *, source=SOURCE, symbols=(),
                            existing_identity=None, expected_legacy_versions=None, missing_only=False):
    """Load valid observations under provider-series keys and verify committed counts."""
    if not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
        raise ValueError("HISTORY_CAPTURE_DIGEST_REQUIRED")
    if source not in (SOURCE, INTERVALORES):
        raise ValueError("HISTORY_CAPTURE_SOURCE_UNVERIFIED")
    if expected_legacy_versions is not None and (
            existing_identity is None or symbols or source != SOURCE or missing_only):
        raise ValueError("HISTORY_MIGRATION_EXISTING_FULL_CAPTURE_REQUIRED")
    member_name = MEMBER if source == SOURCE else INTERVALORES_MEMBER
    selected = set(symbols)
    seen = set()
    totals = Counter()
    rejected = Counter()
    store = None
    schema_upgrade = None
    with Path(archive).open("rb") as capture_file:
        if os.fstat(capture_file.fileno()).st_size > MAX_BYTES:
            raise ValueError("HISTORY_CAPTURE_FILE_TOO_LARGE")
        if file_digest(capture_file, "sha256").hexdigest() != expected_sha256:
            raise ValueError("HISTORY_CAPTURE_FILE_DIGEST_MISMATCH")
        capture_file.seek(0)
        with ZipFile(capture_file) as compressed:
            members = [entry for entry in compressed.infolist() if entry.filename == member_name]
            if len(members) != 1 or members[0].file_size > MAX_BYTES:
                raise ValueError("HISTORY_CAPTURE_MEMBER_INVALID")
            with compressed.open(members[0]) as entries:
                for line in entries:
                    capture = json.loads(line)
                    if selected and capture["symbol"] not in selected:
                        continue
                    key = (capture["market"], capture["family"], capture["symbol"])
                    if key in seen:
                        raise ValueError("HISTORY_CAPTURE_DUPLICATE_SERIES")
                    seen.add(key)
                    candles, invalid, delivered, attempt = _candles(capture, expected_sha256, source=source)
                    totals.update(captures=1, received=delivered)
                    rejected.update(invalid)
                    if candles:
                        if store is None:
                            store = (_source_store(destination) if existing_identity is None else
                                     HistoricalStore(str(destination), source_reported_only=True,
                                                     existing_history_identity=existing_identity))
                            if expected_legacy_versions is not None:
                                schema_upgrade = upgrade_existing_history(
                                    store, expected_versions=expected_legacy_versions)
                        receipt = append_many(store, candles, attempt_key=attempt,
                                              fill_missing_reference=missing_only)
                        for field in ("committed_rows", "versions_appended", "canonical_updates"):
                            totals[field] += receipt[field]
                        if missing_only:
                            totals["already_present"] += receipt["already_present"]
    if selected - {key[2] for key in seen}:
        raise ValueError("HISTORY_CAPTURE_SELECTED_SYMBOL_MISSING")
    if store is None:
        raise ValueError("HISTORY_CAPTURE_NO_USABLE_ROWS")
    with store.connect() as connection:
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("HISTORY_REFERENCE_SQLITE_CHECK_FAILED")
        stored = connection.execute(
            "SELECT count(*) FROM history_canonical_v2 WHERE price_basis=?", (SOURCE_REPORTED,),
        ).fetchone()[0]
        versioned = connection.execute(
            "SELECT count(*) FROM history_versions_v2 WHERE price_basis=?", (SOURCE_REPORTED,),
        ).fetchone()[0]
    return {
        **totals, "canonical_rows": stored, "version_rows": versioned,
        "rejected": dict(rejected), "capture_sha256": expected_sha256,
        "destination": "EXISTING_HISTORY" if existing_identity is not None else "REFERENCE_STORE",
        "price_basis": SOURCE_REPORTED, "readiness_implication": "NONE",
        "quick_check": "ok",
        **({"schema_upgrade": schema_upgrade} if schema_upgrade is not None else {}),
    }


def import_iol_export(archive, destination, expected_sha256, *, symbols=(), existing_identity=None,
                      expected_legacy_versions=None, missing_only=False):
    return import_reference_export(archive, destination, expected_sha256, symbols=symbols,
                                   existing_identity=existing_identity,
                                   expected_legacy_versions=expected_legacy_versions,
                                   missing_only=missing_only)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--source", choices=(SOURCE, INTERVALORES), default=SOURCE)
    parser.add_argument("--symbol", action="append", default=[])
    parser.add_argument("--existing-history-id", metavar="DEVICE:INODE")
    parser.add_argument("--expected-legacy-versions", type=int,
                        help="Explicitly upgrade the existing file from a verified legacy snapshot count")
    parser.add_argument("--missing-only", action="store_true",
                        help="Fill missing provider-series dates and preserve existing references")
    args = parser.parse_args()
    identity = None
    if args.existing_history_id is not None:
        if not re.fullmatch(r"[0-9]+:[1-9][0-9]*", args.existing_history_id):
            parser.error("--existing-history-id requires numeric DEVICE:INODE")
        identity = tuple(map(int, args.existing_history_id.split(":")))
    receipt = import_reference_export(args.archive, args.destination, args.sha256, source=args.source,
                                      symbols=args.symbol, existing_identity=identity,
                                      expected_legacy_versions=args.expected_legacy_versions,
                                      missing_only=args.missing_only)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
