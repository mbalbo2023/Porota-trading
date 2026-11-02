#!/usr/bin/env python3
"""A408 bounded host cache HEADER audit: never DB, provider or order access.

The files may contain private quotes and identities.  Neither their keys nor
values, original timestamps, source paths or credentials leave the host.
Only counters, an age bucket and fixed allowlisted status labels are emitted.
This diagnostic cannot validate PIT, broker availability or PAPER readiness.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import sys


SCHEMA = "rc6.a408.cache-header-census-readonly.v1"
MAX_BYTES = 12 * 1024 * 1024
MAX_RECORDS = 60_000
INPUTS = (
    ("PRIMARY_MARKET", Path("/opt/porota-trading/data/market/primary_last.json"), "PRIMARY"),
    ("PRIMARY_APP", Path("/app/data/market/primary_last.json"), "PRIMARY"),
    ("IOL_SHADOW", Path("/opt/porota-trading/data/market/iol_shadow_latest.json"), "IOL"),
    ("BYMA_PUBLIC", Path("/opt/porota-trading/data/market/rc6_public_sources_latest.json"), "BYMA"),
)
NATIVE_CLOCKS = ("source_at", "provider_observed_at", "provider_timestamp",
                 "timestamp", "trade_at", "tradeDate", "event_at", "date")
RECEIPT_CLOCKS = ("received_at", "captured_at", "capture_observed_at",
                  "observed_at", "collected_at", "refreshed_at")
NUMERIC_FIELDS = ("last", "bid", "ask", "price", "variation_pct", "volume",
                  "cash_volume", "vwap")
METRIC_FIELDS = ("records", "dictionary_records", "identity_fields_complete",
                 "native_clock_present", "receipt_clock_present", "numeric_present")


class CacheRejected(Exception):
    """Only fixed public rejection codes are permitted."""


def _safe_bytes(path: Path, *, max_bytes: int = MAX_BYTES):
    """Open each path component O_NOFOLLOW and anchor one regular file fd."""
    if not path.is_absolute() or ".." in path.parts or not path.name:
        raise CacheRejected("PATH_UNSAFE")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    directory = None
    fd = None
    try:
        directory = os.open("/", flags | os.O_DIRECTORY)
        for component in path.parts[1:-1]:
            successor = os.open(component, flags | os.O_DIRECTORY, dir_fd=directory)
            os.close(directory)
            directory = successor
        fd = os.open(path.name, flags | os.O_NONBLOCK, dir_fd=directory)
        original = os.fstat(fd)
        if not stat.S_ISREG(original.st_mode) or original.st_nlink != 1:
            raise CacheRejected("FILE_UNSAFE")
        if not 0 < original.st_size <= max_bytes:
            raise CacheRejected("SIZE_INVALID")
        parts = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(fd, min(256 * 1024, remaining))
            if not chunk:
                break
            parts.append(chunk)
            remaining -= len(chunk)
        data = b"".join(parts)
        final = os.fstat(fd)
        if (len(data) != original.st_size or len(data) > max_bytes or
                (original.st_dev, original.st_ino, original.st_size, original.st_mtime_ns) !=
                (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns)):
            raise CacheRejected("FILE_CHANGED")
        return data, original
    except FileNotFoundError as exc:
        raise CacheRejected("MISSING") from exc
    except NotADirectoryError as exc:
        raise CacheRejected("PATH_UNSAFE") from exc
    except (PermissionError, OSError) as exc:
        raise CacheRejected("READ_UNAVAILABLE") from exc
    finally:
        if fd is not None:
            os.close(fd)
        if directory is not None:
            os.close(directory)


def _truthy(row: dict, fields: tuple[str, ...]) -> bool:
    return any(row.get(field) not in (None, "") for field in fields)


def _identity_complete(row: dict, *, primary_symbol: bool = False) -> bool:
    family = row.get("instrument_type") or row.get("family") or row.get("asset_type") or row.get("asset_class")
    term = row.get("settlement") or row.get("term") or row.get("settlement_code")
    symbol = primary_symbol or bool(row.get("symbol") or row.get("ticker"))
    return bool(symbol and family and row.get("market") and row.get("currency") and term)


def _records(payload: dict, role: str):
    if role == "PRIMARY":
        values = payload.get("quotes_by_symbol") or payload.get("last_by_symbol")
        if not isinstance(values, dict):
            return [], "NO_PRIMARY_MAP"
        # Symbols are deliberately never copied, logged or returned.
        return [(value, True) for value in values.values()], "PRIMARY_SYMBOL_MAP"
    if role == "IOL":
        rows = payload.get("symbols")
        return ([(row, False) for row in rows], "IOL_SYMBOL_LIST") if isinstance(rows, list) else ([], "NO_IOL_LIST")
    sources = payload.get("sources")
    if not isinstance(sources, list):
        return [], "NO_BYMA_BLOCKS"
    result = []
    for item in sources:
        if isinstance(item, dict) and str(item.get("source") or "").upper() == "BYMA":
            records = item.get("records")
            if isinstance(records, list):
                result.extend((row, False) for row in records)
                if len(result) > MAX_RECORDS:
                    raise CacheRejected("RECORD_LIMIT_EXCEEDED")
    return result, "BYMA_SOURCE_BLOCKS"


def inspect_cache(code: str, path: Path, role: str, *, current: datetime | None = None):
    current = current or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("aware current required")
    result = {"input": code, "status": "NO_VERIFICADO", "shape": "NOT_READ",
              "file_bytes": 0, "mtime_age_seconds": None,
              **{field: 0 for field in METRIC_FIELDS}}
    try:
        raw, info = _safe_bytes(path)
        parsed = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise CacheRejected("JSON_SHAPE_INVALID")
        rows, shape = _records(parsed, role)
        if len(rows) > MAX_RECORDS:
            raise CacheRejected("RECORD_LIMIT_EXCEEDED")
        age = (current - datetime.fromtimestamp(info.st_mtime, timezone.utc)).total_seconds()
        if age < -30:
            raise CacheRejected("FILE_CLOCK_FUTURE")
        result.update(status="HEADER_READ", shape=shape, file_bytes=len(raw),
                      mtime_age_seconds=max(0, round(age)), records=len(rows))
        inherited_receipt = _truthy(parsed, RECEIPT_CLOCKS)
        for raw_row, primary_symbol in rows:
            row = raw_row if isinstance(raw_row, dict) else {"last": raw_row}
            result["dictionary_records"] += isinstance(raw_row, dict)
            result["identity_fields_complete"] += _identity_complete(row, primary_symbol=primary_symbol)
            result["native_clock_present"] += _truthy(row, NATIVE_CLOCKS)
            result["receipt_clock_present"] += inherited_receipt or _truthy(row, RECEIPT_CLOCKS)
            result["numeric_present"] += _truthy(row, NUMERIC_FIELDS)
    except (UnicodeError, ValueError, json.JSONDecodeError):
        result["status"] = "JSON_INVALID"
    except CacheRejected as exc:
        result["status"] = str(exc)
    return result


def inspect_all(*, current: datetime | None = None, inputs=INPUTS):
    current = current or datetime.now(timezone.utc)
    return {"schema": SCHEMA, "read_only": True, "db_opened": False,
            "provider_calls": 0, "order_routes_called": False,
            "admission_authority": False, "runtime_sha_verified": False,
            "source_contract_ready": False, "market_readiness": "NO_VERIFICADO",
            "fixed_paths_only": True,
            "cache_headers": [inspect_cache(code, path, role, current=current)
                              for code, path, role in inputs]}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv != ["host-probe"]:
        print("Usage: cache headers read-only host-probe", file=sys.stderr)
        return 64
    print(json.dumps(inspect_all(), sort_keys=True, separators=(",", ":"), ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
